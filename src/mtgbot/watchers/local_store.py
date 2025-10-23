"""Watch Phoenix local game store feeds for preorder announcements."""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, Iterable, List, Optional

import aiohttp

from mtgbot.models import CardSku, InventoryEvent, ListingSnapshot, Vendor
from mtgbot.watchers.base import Watcher

log = logging.getLogger(__name__)


class PhoenixLocalStoreWatcher(Watcher):
    """Polls configured Phoenix-area store feeds for product availability."""

    def __init__(
        self, session: aiohttp.ClientSession, feed_urls: Iterable[str]
    ) -> None:
        super().__init__(Vendor.LOCAL_STORE)
        self._session = session
        self._feeds = [url for url in feed_urls if url]
        self.poll_interval = 600.0
        self._pending: Deque[InventoryEvent] = deque()
        self._cache: Dict[str, ListingSnapshot] = {}

    async def poll(self) -> Optional[InventoryEvent]:
        if self._pending:
            return self._pending.popleft()

        for feed_url in self._feeds:
            payload = await self._fetch_feed(feed_url)
            if not payload:
                continue
            events = self._diff_feed(feed_url, payload)
            self._pending.extend(events)

        if self._pending:
            return self._pending.popleft()
        return None

    async def _fetch_feed(self, url: str) -> Optional[dict]:
        try:
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    log.warning("Phoenix feed %s returned %s", url, resp.status)
                    return None
                text = await resp.text()
        except aiohttp.ClientError as exc:
            log.warning("Phoenix feed fetch failed for %s: %s", url, exc)
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            log.warning("Phoenix feed %s did not return valid JSON", url)
            return None

    def _diff_feed(self, feed_url: str, payload: dict) -> List[InventoryEvent]:
        products = payload.get("products", [])
        store_name = payload.get("store", "Phoenix LGS")
        contact_url = payload.get("contact_url")
        events: List[InventoryEvent] = []
        for product in products:
            try:
                snapshot = self._snapshot_from_product(
                    feed_url, store_name, product, contact_url
                )
            except ValueError as exc:
                log.debug("Skipping local store product: %s", exc)
                continue
            key = snapshot.sku.oracle_id
            previous = self._cache.get(key)

            if previous is None:
                events.append(
                    InventoryEvent(
                        snapshot=snapshot,
                        previous_snapshot=None,
                        event_type="store_listing",
                        delta_price=None,
                    )
                )
            else:
                event_type = None
                delta_price: Optional[float] = None
                if snapshot.available and not previous.available:
                    event_type = "store_restock"
                elif snapshot.available != previous.available:
                    event_type = "store_availability_change"
                elif (
                    snapshot.price is not None
                    and previous.price is not None
                    and abs(snapshot.price - previous.price) >= 0.01
                ):
                    event_type = "store_price_change"
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

    def _snapshot_from_product(
        self,
        feed_url: str,
        store_name: str,
        product: dict,
        contact_url: Optional[str],
    ) -> ListingSnapshot:
        identifier = str(product.get("id") or product.get("sku") or product.get("name"))
        if not identifier:
            raise ValueError("Product lacks identifier")
        url = product.get("url") or feed_url
        price = float(product.get("price", 0.0))
        available = bool(product.get("available", False))
        tags = product.get("tags", [])

        return ListingSnapshot(
            vendor=Vendor.LOCAL_STORE,
            sku=CardSku(
                oracle_id=f"{store_name.lower().replace(' ', '-')}-{identifier}",
                product_code=str(product.get("sku") or identifier),
                finish=str(product.get("finish", "any")),
                set_code=product.get("set"),
                collector_number=product.get("collector_number"),
                vendor_sku=str(identifier),
            ),
            title=product.get("name") or identifier,
            url=url,
            price=price,
            currency=product.get("currency", "USD"),
            available=available,
            observed_at=datetime.now(timezone.utc),
            metadata={
                "store": store_name,
                "tags": ",".join(tags) if tags else "",
                "contact_url": product.get("contact_url") or contact_url or url,
            },
        )
