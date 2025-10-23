"""Service for fetching and charting TCGplayer latest sales."""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List, Optional

import aiohttp
import matplotlib

from mtgbot.config import VendorSettings
from mtgbot.storage.tcgplayer_sales import SaleRecord, TcgplayerSalesRepository

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402


log = logging.getLogger(__name__)


class TcgSalesError(RuntimeError):
    """Raised when TCGplayer requests fail."""


@dataclass(slots=True)
class SalesSummary:
    tcg_id: int
    title: str
    latest_price: float
    oldest_price: float
    gain: float
    total_sales: int
    time_span_days: int


class TcgplayerSalesService:
    BASE_URL = "https://mpapi.tcgplayer.com/v2"
    CHUNK_SIZE = 25

    def __init__(
        self,
        repository: TcgplayerSalesRepository,
        vendor_settings: VendorSettings,
    ) -> None:
        self._repository = repository
        self._settings = vendor_settings
        self._session: Optional[aiohttp.ClientSession] = None
        self._token: Optional[str] = None
        self._token_expiry: datetime | None = None
        self._token_lock = asyncio.Lock()

    def set_session(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def fetch_and_store_sales(
        self,
        tcg_id: int,
        *,
        max_listings: int = 100,
        days: int = 90,
        languages: Optional[Iterable[int]] = None,
        variants: Optional[Iterable[int]] = None,
        conditions: Optional[Iterable[int]] = None,
    ) -> List[SaleRecord]:
        session = self._session
        if session is None:
            raise TcgSalesError("HTTP session not available")

        raw_sales = await self._fetch_sales_from_api(
            session,
            tcg_id,
            max_listings=max_listings,
            languages=languages,
            variants=variants,
            conditions=conditions,
        )

        cutoff = datetime.utcnow() - timedelta(days=max(days, 1))
        records: List[SaleRecord] = []
        for item in raw_sales:
            order_date = _parse_date(item.get("orderDate"))
            if order_date is None or order_date < cutoff:
                continue
            records.append(
                SaleRecord(
                    tcg_id=tcg_id,
                    title=item.get("title") or "TCGplayer Listing",
                    order_datetime=order_date,
                    quantity=int(item.get("quantity") or 0),
                    price=float(item.get("purchasePrice") or 0.0),
                )
            )

        if not records:
            return []

        await self._repository.upsert_sales(records)
        # Retrieve combined view from storage to include prior data
        stored = await self._repository.list_sales(
            tcg_id, within_days=days, limit=max_listings
        )
        return stored or records

    async def build_chart(
        self, records: List[SaleRecord]
    ) -> tuple[io.BytesIO, SalesSummary]:
        if not records:
            raise TcgSalesError("No sales data available")

        sorted_records = sorted(records, key=lambda r: r.order_datetime)
        dates = [record.order_datetime for record in sorted_records]
        prices = [record.price for record in sorted_records]

        fig, ax = plt.subplots(figsize=(6, 3.5), dpi=150)
        ax.plot(dates, prices, marker="o", linewidth=1, markersize=3)
        ax.set_xlabel("Date")
        ax.set_ylabel("Price (USD)")
        ax.grid(True, axis="y", linestyle="--", alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        fig.autofmt_xdate()

        latest_price = prices[-1]
        oldest_price = prices[0]
        gain = latest_price - oldest_price
        title = sorted_records[-1].title
        ax.set_title(title, fontsize=10)

        buffer = io.BytesIO()
        fig.tight_layout()
        fig.savefig(buffer, format="png")
        buffer.seek(0)
        plt.close(fig)

        summary = SalesSummary(
            tcg_id=sorted_records[-1].tcg_id,
            title=title,
            latest_price=latest_price,
            oldest_price=oldest_price,
            gain=gain,
            total_sales=len(sorted_records),
            time_span_days=(dates[-1] - dates[0]).days or 1,
        )
        return buffer, summary

    async def _fetch_sales_from_api(
        self,
        session: aiohttp.ClientSession,
        tcg_id: int,
        *,
        max_listings: int,
        languages: Optional[Iterable[int]],
        variants: Optional[Iterable[int]],
        conditions: Optional[Iterable[int]],
    ) -> List[dict[str, object]]:
        url = f"{self.BASE_URL}/product/{tcg_id}/latestsales"
        offset = 0
        aggregated: List[dict[str, object]] = []
        token = await self._ensure_token(session)
        headers = {"Content-Type": "application/json"}
        if self._settings.tcgplayer_cookie:
            headers["Cookie"] = self._settings.tcgplayer_cookie
        if token:
            headers["Authorization"] = f"Bearer {token}"

        payload = {
            "limit": self.CHUNK_SIZE,
            "listingType": "All",
            "offset": offset,
            "languages": list(languages) if languages else [1],
            "variants": list(variants) if variants else [1],
            "conditions": list(conditions) if conditions else [1, 2],
        }

        while True:
            try:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 401:
                        token = await self._refresh_token(session)
                        if token:
                            headers["Authorization"] = f"Bearer {token}"
                        elif "Authorization" in headers:
                            headers.pop("Authorization")
                        continue
                    resp.raise_for_status()
                    data = await resp.json()
            except aiohttp.ClientResponseError as exc:
                raise TcgSalesError(
                    f"TCGplayer request failed with status {exc.status}"
                ) from exc
            except aiohttp.ClientError as exc:  # pragma: no cover - network failure
                raise TcgSalesError("TCGplayer request failed") from exc

            chunk = data.get("data", []) or []
            aggregated.extend(chunk)
            if len(chunk) < self.CHUNK_SIZE:
                break
            offset += self.CHUNK_SIZE
            if offset >= max_listings:
                break
            payload["offset"] = offset

        return aggregated

    async def _ensure_token(self, session: aiohttp.ClientSession) -> Optional[str]:
        if not self._settings.tcgplayer_public_key or not self._settings.tcgplayer_private_key:
            return None
        if self._token and self._token_expiry and self._token_expiry > datetime.utcnow():
            return self._token
        async with self._token_lock:
            if (
                self._token
                and self._token_expiry
                and self._token_expiry > datetime.utcnow()
            ):
                return self._token
            return await self._refresh_token(session)

    async def _refresh_token(self, session: aiohttp.ClientSession) -> Optional[str]:
        if not self._settings.tcgplayer_public_key or not self._settings.tcgplayer_private_key:
            return None
        data = {
            "grant_type": "client_credentials",
            "client_id": self._settings.tcgplayer_public_key,
            "client_secret": self._settings.tcgplayer_private_key,
        }
        try:
            async with session.post("https://api.tcgplayer.com/token", data=data) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    log.warning("TCGplayer token request failed: %s", text)
                    return None
                payload = await resp.json()
        except aiohttp.ClientError as exc:  # pragma: no cover - network failure
            log.warning("TCGplayer token request error: %s", exc)
            return None

        token = payload.get("access_token")
        expires_in = int(payload.get("expires_in", 1200))
        if token:
            self._token = token
            self._token_expiry = datetime.utcnow() + timedelta(seconds=expires_in - 30)
        return token


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


__all__ = [
    "TcgplayerSalesService",
    "SalesSummary",
    "TcgSalesError",
]
