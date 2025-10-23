"""Watcher that pulls MTG set data from Scryfall."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import List, Optional

import aiohttp

from mtgbot.models import MagicSet

log = logging.getLogger(__name__)

SCRYFALL_SETS_URL = "https://api.scryfall.com/sets"
RELEVANT_SET_TYPES = {
    "expansion",
    "core",
    "masters",
    "commander",
    "draft_innovation",
    "planechase",
    "starter",
    "spellbook",
    "alchemy",
    "funny",
    "box",
    "token",
    "minigame",
    "promo",
    "arsenal",
}


class ScryfallSetWatcher:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def fetch_sets(self) -> List[MagicSet]:
        url: Optional[str] = SCRYFALL_SETS_URL
        today = date.today()
        cutoff = today - timedelta(days=365)
        seen: List[MagicSet] = []

        while url:
            try:
                async with self._session.get(url) as resp:
                    if resp.status != 200:
                        log.warning("Scryfall sets endpoint returned %s", resp.status)
                        break
                    payload = await resp.json()
            except aiohttp.ClientError as exc:
                log.warning("Failed to fetch Scryfall sets: %s", exc)
                break

            data = payload.get("data", [])
            for item in data:
                set_type = item.get("set_type")
                if set_type not in RELEVANT_SET_TYPES:
                    continue
                released_at = _parse_date(item.get("released_at"))
                if released_at and released_at < cutoff:
                    # Skip sets released over a year ago to reduce noise.
                    continue
                magic_set = MagicSet(
                    set_id=item["id"],
                    code=item["code"],
                    name=item["name"],
                    set_type=set_type or "unknown",
                    released_at=released_at,
                    scryfall_uri=item.get("scryfall_uri", ""),
                    icon_svg_uri=item.get("icon_svg_uri"),
                    observed_at=today,
                )
                seen.append(magic_set)

            url = payload.get("next_page")

        return seen


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
