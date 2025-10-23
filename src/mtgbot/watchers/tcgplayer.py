"""Watcher for TCGplayer API pricing and availability."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, Iterable, List, Optional

import aiohttp

from mtgbot.models import CardSku, InventoryEvent, ListingSnapshot, Vendor
from mtgbot.watchers.base import Watcher

log = logging.getLogger(__name__)


class TcgplayerWatcher(Watcher):
    """Polls TCGplayer pricing endpoints for SKU availability."""

    AUTH_URL = "https://api.tcgplayer.com/token"
    SKU_URL = "https://api.tcgplayer.com/pricing/sku/{sku_id}"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        public_key: str,
        private_key: str,
        sku_whitelist: Iterable[str],
    ) -> None:
        super().__init__(Vendor.TCGPLAYER)
        self._session = session
        self._public_key = public_key
        self._private_key = private_key
        self._skus = [sku for sku in sku_whitelist if sku]
        self.poll_interval = 120.0
        self._pending: Deque[InventoryEvent] = deque()
        self._cache: Dict[str, ListingSnapshot] = {}
        self._token: Optional[str] = None
        self._token_expiry: datetime = datetime.now(timezone.utc)
        self._token_lock = asyncio.Lock()

    async def poll(self) -> Optional[InventoryEvent]:
        if not self._skus:
            log.debug("TCGplayer watcher has no SKU whitelist configured")
            return None

        if self._pending:
            return self._pending.popleft()

        token = await self._ensure_token()
        if not token:
            return None

        for sku in self._skus:
            payload = await self._fetch_sku(token, sku)
            if not payload:
                continue
            snapshot = self._snapshot_from_payload(sku, payload)
            events = self._diff_snapshot(snapshot)
            self._pending.extend(events)

        if self._pending:
            return self._pending.popleft()
        return None

    async def _ensure_token(self) -> Optional[str]:
        async with self._token_lock:
            now = datetime.now(timezone.utc)
            if self._token and now < self._token_expiry:
                return self._token
            try:
                async with self._session.post(
                    self.AUTH_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._public_key,
                        "client_secret": self._private_key,
                    },
                ) as resp:
                    if resp.status != 200:
                        log.warning(
                            "TCGplayer auth failed with status %s", resp.status
                        )
                        return None
                    data = await resp.json()
            except aiohttp.ClientError as exc:
                log.warning("TCGplayer auth request failed: %s", exc)
                return None

            token = data.get("access_token")
            expires = data.get("expires_in", 0)
            if not token:
                log.warning("TCGplayer auth response missing token")
                return None
            self._token = token
            self._token_expiry = now + timedelta(seconds=int(expires) - 30)
            return token

    async def _fetch_sku(self, token: str, sku_id: str) -> Optional[dict]:
        url = self.SKU_URL.format(sku_id=sku_id)
        headers = {
            "Authorization": f"bearer {token}",
            "Accept": "application/json",
        }
        try:
            async with self._session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    log.debug("TCGplayer SKU %s returned %s", sku_id, resp.status)
                    return None
                data = await resp.json()
        except aiohttp.ClientError as exc:
            log.debug("TCGplayer SKU fetch failed for %s: %s", sku_id, exc)
            return None
        results = data.get("results")
        if not results:
            return None
        return results[0]

    def _snapshot_from_payload(self, sku_id: str, payload: dict) -> ListingSnapshot:
        product_id = payload.get("productId")
        title = payload.get("productName") or f"TCG SKU {sku_id}"
        url = payload.get("productUrl") or (
            f"https://www.tcgplayer.com/product/{product_id}" if product_id else ""
        )
        price = float(
            payload.get("marketPrice")
            or payload.get("directLowPrice")
            or payload.get("lowestListingPrice")
            or 0.0
        )
        quantity = payload.get("quantity", 0)
        available = bool(quantity and quantity > 0)

        return ListingSnapshot(
            vendor=Vendor.TCGPLAYER,
            sku=CardSku(
                oracle_id=str(sku_id),
                product_code=str(payload.get("skuId") or sku_id),
                finish=str(payload.get("printing") or "any").lower(),
                set_code=payload.get("setCode"),
                collector_number=payload.get("number"),
                vendor_sku=str(payload.get("skuId") or sku_id),
            ),
            title=title,
            url=url,
            price=price,
            currency=payload.get("currencyCode") or "USD",
            available=available,
            observed_at=datetime.now(timezone.utc),
            metadata={
                "marketPrice": payload.get("marketPrice"),
                "directLowPrice": payload.get("directLowPrice"),
                "quantity": quantity,
            },
        )

    def _diff_snapshot(self, snapshot: ListingSnapshot) -> List[InventoryEvent]:
        key = snapshot.sku.oracle_id
        previous = self._cache.get(key)
        events: List[InventoryEvent] = []

        if previous is None:
            events.append(
                InventoryEvent(
                    snapshot=snapshot,
                    previous_snapshot=None,
                    event_type="tcgplayer_listing",
                    delta_price=None,
                )
            )
        else:
            event_type = None
            delta_price: Optional[float] = None
            if snapshot.available and not previous.available:
                event_type = "tcgplayer_restock"
            elif snapshot.available != previous.available:
                event_type = "tcgplayer_availability_change"
            elif abs(snapshot.price - previous.price) >= 0.01:
                event_type = "tcgplayer_price_change"
                delta_price = snapshot.price - previous.price

            if event_type:
                events.append(
                    InventoryEvent(
                        snapshot=snapshot,
                        previous_snapshot=previous,
                        event_type=event_type,
                        delta_price=delta_price,
                    )
                )

        self._cache[key] = snapshot
        return events
