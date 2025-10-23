"""Card Kingdom watcher implementation with HTML scraping and dedupe."""

from __future__ import annotations

import logging
import re
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional

import aiohttp
from bs4 import BeautifulSoup

from mtgbot.models import CardSku, InventoryEvent, ListingSnapshot, Vendor
from mtgbot.watchers.base import Watcher

log = logging.getLogger(__name__)


class CardKingdomWatcher(Watcher):
    """Polls Card Kingdom preorder listings for availability changes."""

    BASE_URL = "https://www.cardkingdom.com"
    PREORDER_PATH = "/catalog/preorder"

    def __init__(self, session: aiohttp.ClientSession) -> None:
        super().__init__(Vendor.CARD_KINGDOM)
        self._session = session
        self.poll_interval = 180.0
        self._pending_events: Deque[InventoryEvent] = deque()
        self._snapshot_cache: Dict[str, ListingSnapshot] = {}

    async def poll(self) -> Optional[InventoryEvent]:
        if self._pending_events:
            return self._pending_events.popleft()

        html = await self._fetch_preorders()
        if html is None:
            return None

        snapshots = self._parse_listings(html)
        events = self._diff_snapshots(snapshots)
        self._pending_events.extend(events)

        if self._pending_events:
            return self._pending_events.popleft()
        return None

    async def _fetch_preorders(self) -> Optional[str]:
        url = f"{self.BASE_URL}{self.PREORDER_PATH}"
        try:
            async with self._session.get(url, headers=_DEFAULT_HEADERS()) as resp:
                if resp.status != 200:
                    log.warning(
                        "Card Kingdom returned HTTP %s for %s", resp.status, url
                    )
                    return None
                return await resp.text()
        except aiohttp.ClientError as exc:
            log.warning("Card Kingdom fetch failed: %s", exc)
            return None

    def _parse_listings(self, html: str) -> List[ListingSnapshot]:
        soup = BeautifulSoup(html, "html.parser")
        nodes = soup.select("[data-product-id]")
        snapshots: List[ListingSnapshot] = []
        if not nodes:
            log.debug("Card Kingdom preorder markup missing product nodes")
        for node in nodes:
            try:
                snapshot = self._snapshot_from_node(node)
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("Failed to parse preorder node: %s", exc)
                continue
            snapshots.append(snapshot)
        return snapshots

    def _snapshot_from_node(self, node) -> ListingSnapshot:
        product_id = node.get("data-product-id") or ""
        product_code = node.get("data-product-sku") or product_id
        link = node.select_one("a[href]")
        url = self._resolve_url(link["href"] if link else "")
        title_el = (
            node.select_one(".productDetailTitle")
            or node.select_one(".productCardHeader")
            or node.select_one("h2")
            or link
        )
        title = title_el.get_text(strip=True) if title_el else "Card Kingdom Listing"

        price = _extract_price(
            node.get("data-price")
            or node.get("data-price-each")
            or node.get_text(strip=True)
        )
        available = _is_available(node)
        edition = node.get("data-edition")
        collector = node.get("data-collector-number")
        finish = (node.get("data-finish") or "nonfoil").lower()

        snapshot = ListingSnapshot(
            vendor=self.vendor,
            sku=CardSku(
                oracle_id=self._sku_key(product_code, collector, finish),
                product_code=product_code,
                finish=finish,
                collector_number=collector,
                set_code=edition,
                vendor_sku=str(product_id),
            ),
            title=title,
            url=url,
            price=price,
            currency="USD",
            available=available,
            observed_at=datetime.now(timezone.utc),
            metadata={
                "product_id": product_id,
                "edition": edition or "",
                "collector": collector or "",
            },
        )
        return snapshot

    def _diff_snapshots(self, snapshots: List[ListingSnapshot]) -> List[InventoryEvent]:
        events: List[InventoryEvent] = []
        for snapshot in snapshots:
            key = snapshot.sku.oracle_id
            previous = self._snapshot_cache.get(key)
            if previous is None:
                events.append(
                    InventoryEvent(
                        snapshot=snapshot,
                        previous_snapshot=None,
                        event_type="new_listing",
                        delta_price=None,
                    )
                )
                self._snapshot_cache[key] = snapshot
                continue

            event_type = None
            delta_price: Optional[float] = None

            if snapshot.available and not previous.available:
                event_type = "restock"
            elif snapshot.available != previous.available:
                event_type = "availability_change"
            elif abs(snapshot.price - previous.price) >= 0.01:
                event_type = "price_change"
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

            self._snapshot_cache[key] = snapshot

        return events

    def _resolve_url(self, href: str) -> str:
        if href.startswith("http"):
            return href
        return f"{self.BASE_URL}{href}"

    def _sku_key(self, product_code: str, collector: Optional[str], finish: str) -> str:
        parts = [part for part in [product_code, collector, finish] if part]
        return "|".join(parts) if parts else product_code


def _extract_price(raw: Optional[str]) -> float:
    if not raw:
        return 0.0
    match = re.search(r"(\d+(\.\d{1,2})?)", raw.replace(",", ""))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return 0.0
    return 0.0


def _is_available(node) -> bool:
    availability_texts = [
        element.get_text(strip=True).lower()
        for element in node.select(".productDetailAvailability, .productStatus, .status")
    ]
    for text in availability_texts:
        if "out of stock" in text or "sold out" in text:
            return False
    button = node.select_one("button, a.button")
    if button and "add to cart" in button.get_text(strip=True).lower():
        return True
    return True


def _DEFAULT_HEADERS() -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }
