"""Base classes for retailer and signal watchers."""

from __future__ import annotations

import abc
from typing import AsyncIterator, Optional

from mtgbot.models import InventoryEvent, Vendor


class Watcher(abc.ABC):
    """Common interface for inventory/release watchers."""

    poll_interval: float = 60.0
    vendor: Vendor

    def __init__(self, vendor: Vendor):
        self.vendor = vendor

    @abc.abstractmethod
    async def poll(self) -> Optional[InventoryEvent]:
        """Return the next interesting event or None when idle."""

    async def stream(self) -> AsyncIterator[InventoryEvent]:
        """Continuous stream of inventory events."""
        while True:
            event = await self.poll()
            if event is None:
                return
            yield event
