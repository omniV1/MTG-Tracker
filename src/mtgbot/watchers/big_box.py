"""Watcher for big-box retailer product pages."""

from __future__ import annotations

import logging
import re
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, Iterable, List, Optional

import aiohttp

from mtgbot.models import CardSku, InventoryEvent, ListingSnapshot, Vendor
from mtgbot.watchers.base import Watcher

log = logging.getLogger(__name__)


class BigBoxWatcher(Watcher):
    """Poll configured big-box product pages (Amazon, Target, etc.)."""

    def __init__(
        self, session: aiohttp.ClientSession, product_urls: Iterable[str]
    ) -> None:
        super().__init__(Vendor.AMAZON)
        self._session = session
        self._urls = [url for url in product_urls if url]
        self.poll_interval = 420.0
        self._pending: Deque[InventoryEvent] = deque()
        self._cache: Dict[str, ListingSnapshot] = {}

    async def poll(self) -> Optional[InventoryEvent]:
        if self._pending:
            return self._pending.popleft()

        for url in self._urls:
            html = await self._fetch_page(url)
            if not html:
                continue
            snapshot = self._snapshot_from_html(url, html)
            events = self._diff_snapshot(snapshot)
            self._pending.extend(events)

        if self._pending:
            return self._pending.popleft()
        return None

    async def _fetch_page(self, url: str) -> Optional[str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0 Safari/537.36"
            ),
        }
        try:
            async with self._session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    log.debug("Big-box url %s returned %s", url, resp.status)
                    return None
                return await resp.text()
        except aiohttp.ClientError as exc:
            log.debug("Big-box fetch failed for %s: %s", url, exc)
            return None

    def _snapshot_from_html(self, url: str, html: str) -> ListingSnapshot:
        vendor = _vendor_from_url(url)
        title = _extract_title(html) or f"{vendor.value.title()} listing"
        price = _extract_price(html)
        available = _is_in_stock(html)

        return ListingSnapshot(
            vendor=vendor,
            sku=CardSku(
                oracle_id=url,
                product_code=url,
                finish="any",
            ),
            title=title,
            url=url,
            price=price,
            currency="USD",
            available=available,
            observed_at=datetime.now(timezone.utc),
            metadata={"source": "big_box"},
        )

    def _diff_snapshot(self, snapshot: ListingSnapshot) -> List[InventoryEvent]:
        key = snapshot.url
        previous = self._cache.get(key)
        events: List[InventoryEvent] = []

        if previous is None:
            events.append(
                InventoryEvent(
                    snapshot=snapshot,
                    previous_snapshot=None,
                    event_type="big_box_listing",
                    delta_price=None,
                )
            )
        else:
            event_type = None
            delta_price: Optional[float] = None
            if snapshot.available and not previous.available:
                event_type = "big_box_restock"
            elif snapshot.available != previous.available:
                event_type = "big_box_availability_change"
            elif abs(snapshot.price - previous.price) >= 0.01:
                event_type = "big_box_price_change"
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


def _vendor_from_url(url: str) -> Vendor:
    url_lower = url.lower()
    if "amazon." in url_lower:
        return Vendor.AMAZON
    if "target.com" in url_lower:
        return Vendor.TARGET
    if "bestbuy.com" in url_lower:
        return Vendor.BEST_BUY
    if "walmart.com" in url_lower:
        return Vendor.WALMART
    return Vendor.LOCAL_STORE


def _extract_title(html: str) -> Optional[str]:
    match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def _extract_price(html: str) -> float:
    match = re.search(r"\$(\d+[.,]?\d*)", html)
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            return 0.0
    return 0.0


def _is_in_stock(html: str) -> bool:
    lowered = html.lower()
    if "currently unavailable" in lowered or "out of stock" in lowered:
        return False
    if "sold out" in lowered or "temporarily unavailable" in lowered:
        return False
    return "in stock" in lowered or "pre-order" in lowered or "add to cart" in lowered
