"""Application entrypoint wiring settings, watchers, and Discord bot."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Iterable, List, Sequence

import aiohttp
from datetime import datetime, time, timedelta, timezone

from mtgbot.config import load_settings
from mtgbot.models import Decision, InventoryEvent, WishlistEntry
from mtgbot.notifications.discord_bot import MtgDiscordBot, start_bot
from mtgbot.services.set_schedule import SetScheduleService
from mtgbot.services.wishlist import WishlistService
from mtgbot.storage.sets import SetRepository
from mtgbot.storage.wishlist import RoleMappingRepository, WishlistRepository
from mtgbot.watchers.base import Watcher
from mtgbot.watchers.card_kingdom import CardKingdomWatcher
from mtgbot.watchers.local_store import PhoenixLocalStoreWatcher
from mtgbot.watchers.tcgplayer import TcgplayerWatcher
from mtgbot.watchers.big_box import BigBoxWatcher
from mtgbot.watchers.scryfall_sets import ScryfallSetWatcher

log = logging.getLogger(__name__)


class DecisionEngine:
    """Matches inventory events against wishlists and selects actions."""

    def __init__(self) -> None:
        self._wishlists: dict[str, List[WishlistEntry]] = defaultdict(list)

    def register(self, wishlist: WishlistEntry) -> None:
        key = wishlist.sku.oracle_id
        entries = self._wishlists[key]
        for index, existing in enumerate(entries):
            if existing.discord_user_id == wishlist.discord_user_id:
                entries[index] = wishlist
                break
        else:
            entries.append(wishlist)

    def unregister(self, discord_user_id: int, oracle_id: str) -> None:
        entries = self._wishlists.get(oracle_id)
        if not entries:
            return
        self._wishlists[oracle_id] = [
            entry
            for entry in entries
            if entry.discord_user_id != discord_user_id
        ]
        if not self._wishlists[oracle_id]:
            del self._wishlists[oracle_id]

    def reset(self, entries: Sequence[WishlistEntry]) -> None:
        self._wishlists.clear()
        for entry in entries:
            self.register(entry)

    def evaluate(self, event: InventoryEvent) -> Iterable[Decision]:
        snap = event.snapshot
        matches = self._wishlists.get(snap.sku.oracle_id, [])
        for wishlist in matches:
            if wishlist.preferred_vendors and snap.vendor not in wishlist.preferred_vendors:
                continue
            if (
                wishlist.max_price is not None
                and snap.price > wishlist.max_price
            ):
                continue
            yield Decision(
                event=event,
                wishlist=wishlist,
                action=wishlist.action_preference,
                rationale=f"Price {snap.price:.2f} within threshold",
            )


async def inventory_worker(
    watcher: Watcher, queue: asyncio.Queue[InventoryEvent]
) -> None:
    try:
        while True:
            try:
                event = await watcher.poll()
            except Exception as exc:
                log.exception("Watcher %s failed: %s", watcher.vendor.value, exc)
                await asyncio.sleep(min(300, watcher.poll_interval * 2))
                continue
            if event is not None:
                await queue.put(event)
            await asyncio.sleep(watcher.poll_interval)
    except asyncio.CancelledError:
        log.info("Inventory worker for %s cancelled", watcher.vendor.value)
        raise


async def decision_worker(
    queue: asyncio.Queue[InventoryEvent],
    engine: DecisionEngine,
    discord_client: MtgDiscordBot,
) -> None:
    try:
        while True:
            event = await queue.get()
            try:
                decisions = list(engine.evaluate(event))
                if not decisions:
                    continue
                log.info("Dispatching %d decisions", len(decisions))
                await discord_client.send_decisions(decisions)
            finally:
                queue.task_done()
    except asyncio.CancelledError:
        log.info("Decision worker cancelled")
        raise


async def app() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()

    event_queue: asyncio.Queue[InventoryEvent] = asyncio.Queue()
    engine = DecisionEngine()

    wishlist_repo = WishlistRepository(settings.database.sqlite_path)
    role_repo = RoleMappingRepository(settings.database.sqlite_path)
    wishlist_service = WishlistService(wishlist_repo, role_repo, engine)
    await wishlist_service.initialize()

    set_repo = SetRepository(settings.database.sqlite_path)
    set_schedule_service = SetScheduleService(set_repo)
    await set_schedule_service.initialize()

    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        watchers: List[Watcher] = [
            CardKingdomWatcher(session),
        ]

        if settings.vendors.phoenix_store_feeds:
            watchers.append(
                PhoenixLocalStoreWatcher(
                    session, settings.vendors.phoenix_store_feeds
                )
            )

        if settings.vendors.tcgplayer_public_key and settings.vendors.tcgplayer_private_key:
            watchers.append(
                TcgplayerWatcher(
                    session,
                    public_key=settings.vendors.tcgplayer_public_key,
                    private_key=settings.vendors.tcgplayer_private_key,
                    sku_whitelist=settings.vendors.tcgplayer_skus,
                )
            )

        if settings.vendors.big_box_urls:
            watchers.append(
                BigBoxWatcher(session, settings.vendors.big_box_urls)
            )

        scryfall_watcher = ScryfallSetWatcher(session)

        discord_client = MtgDiscordBot(
            settings, wishlist_service, set_schedule_service
        )
        discord_client.register_commands()

        async def sync_sets_job() -> None:
            sets = await scryfall_watcher.fetch_sets()
            if sets:
                await set_schedule_service.sync_sets(sets)
            alerts = await set_schedule_service.pending_alerts()
            if not alerts:
                return
            await discord_client.send_set_alerts(alerts)
            for alert in alerts:
                await set_schedule_service.mark_alert_sent(alert)

        async def digest_job() -> None:
            upcoming = await set_schedule_service.upcoming_sets(within_days=90)
            await discord_client.send_set_digest(
                upcoming, title="MTG Release Digest (Next 90 Days)"
            )

        async def set_sync_loop() -> None:
            try:
                interval_minutes = max(
                    settings.schedule.scryfall_poll_interval_minutes, 30
                )
                interval_seconds = interval_minutes * 60
                await sync_sets_job()
                while True:
                    await asyncio.sleep(interval_seconds)
                    await sync_sets_job()
            except asyncio.CancelledError:
                raise

        async def digest_loop() -> None:
            try:
                while True:
                    await _sleep_until_next_digest(
                        settings.schedule.digest_hour_utc,
                        settings.schedule.digest_minute_utc,
                    )
                    await digest_job()
            except asyncio.CancelledError:
                raise

        async def _sleep_until_next_digest(hour: int, minute: int) -> None:
            now = datetime.now(timezone.utc)
            digest_time = time(hour=hour % 24, minute=minute % 60, tzinfo=timezone.utc)
            target = datetime.combine(now.date(), digest_time)
            if target <= now:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())

        tasks = [
            asyncio.create_task(start_bot(discord_client)),
            asyncio.create_task(
                decision_worker(event_queue, engine, discord_client)
            ),
            asyncio.create_task(set_sync_loop()),
            asyncio.create_task(digest_loop()),
        ]

        tasks.extend(
            asyncio.create_task(inventory_worker(watcher, event_queue))
            for watcher in watchers
        )

        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    try:
        asyncio.run(app())
    except KeyboardInterrupt:
        log.info("Shutting down MTG bot")


if __name__ == "__main__":
    main()
