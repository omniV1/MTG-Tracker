"""Service for retrieving TCGplayer marketplace listings."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional

import aiohttp


log = logging.getLogger(__name__)


@dataclass(slots=True)
class ListingInfo:
    sku: int
    seller_key: str
    price: float
    quantity: int
    is_direct: bool
    channel_id: int
    seller_level: Optional[str]
    seller_name: Optional[str]


class TcgplayerListingsService:
    BASE_URL = "https://mpapi.tcgplayer.com/v2"
    DEFAULT_MPFEV = "4426"

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None

    def set_session(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def fetch_listings(
        self,
        product_id: int,
        *,
        limit: int = 20,
        offset: int = 0,
        include_direct: bool = True,
        include_marketplace: bool = True,
    ) -> List[ListingInfo]:
        session = self._session
        if session is None:
            raise RuntimeError("HTTP session not available")

        params = {
            "mpfev": self.DEFAULT_MPFEV,
            "offset": max(0, offset),
            "limit": max(1, min(limit, 100)),
        }
        if not include_direct:
            params["includeDirect"] = "false"
        if not include_marketplace:
            params["includeMarketplace"] = "false"

        url = f"{self.BASE_URL}/product/{product_id}/listings/marketplace"
        headers = {
            "Origin": "https://www.tcgplayer.com",
            "Referer": "https://www.tcgplayer.com/",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/141.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        }

        try:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    log.warning(
                        "Listings fetch failed for product %s (%s): %s",
                        product_id,
                        resp.status,
                        text,
                    )
                    return []
                data = await resp.json()
        except aiohttp.ClientError as exc:  # pragma: no cover - network failure
            log.warning("Listings request failed: %s", exc)
            return []

        results = data.get("results")
        if not isinstance(results, list):
            return []

        listings: List[ListingInfo] = []
        for entry in results:
            try:
                sku = int(entry.get("productSkuId"))
                seller_key = str(entry.get("sellerKey"))
                price = float(entry.get("price") or entry.get("lowPrice") or 0.0)
                quantity = int(entry.get("quantityAvailable") or entry.get("quantity") or 0)
                is_direct = bool(entry.get("isDirect"))
                channel_id = int(entry.get("channelId") or 0)
                seller = entry.get("seller") or {}
                seller_level = seller.get("level") if isinstance(seller, dict) else None
                seller_name = seller.get("name") if isinstance(seller, dict) else None
            except (TypeError, ValueError):
                continue
            listings.append(
                ListingInfo(
                    sku=sku,
                    seller_key=seller_key,
                    price=price,
                    quantity=quantity,
                    is_direct=is_direct,
                    channel_id=channel_id,
                    seller_level=seller_level,
                    seller_name=seller_name,
                )
            )

        return listings


__all__ = ["TcgplayerListingsService", "ListingInfo"]
