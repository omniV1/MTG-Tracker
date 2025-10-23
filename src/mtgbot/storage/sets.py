"""SQLite repositories for MTG set schedules and notification state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, List

import aiosqlite

from mtgbot.models import MagicSet, SetMilestone


@dataclass(slots=True)
class SetState:
    magic_set: MagicSet
    notified_announcement: bool
    notified_t_minus_30: bool
    notified_t_minus_14: bool
    notified_t_minus_7: bool
    notified_t_minus_1: bool
    notified_release_day: bool


class SetRepository:
    def __init__(self, sqlite_path: str) -> None:
        self._sqlite_path = sqlite_path

    async def init(self) -> None:
        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS mtg_sets (
                    set_id TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    set_type TEXT NOT NULL,
                    released_at TEXT,
                    scryfall_uri TEXT NOT NULL,
                    icon_svg_uri TEXT,
                    observed_at TEXT NOT NULL,
                    notified_announcement INTEGER DEFAULT 0,
                    notified_t_minus_30 INTEGER DEFAULT 0,
                    notified_t_minus_14 INTEGER DEFAULT 0,
                    notified_t_minus_7 INTEGER DEFAULT 0,
                    notified_t_minus_1 INTEGER DEFAULT 0,
                    notified_release_day INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.commit()

    async def upsert_set(self, magic_set: MagicSet) -> None:
        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(
                """
                INSERT INTO mtg_sets (
                    set_id,
                    code,
                    name,
                    set_type,
                    released_at,
                    scryfall_uri,
                    icon_svg_uri,
                    observed_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(set_id) DO UPDATE SET
                    code = excluded.code,
                    name = excluded.name,
                    set_type = excluded.set_type,
                    released_at = excluded.released_at,
                    scryfall_uri = excluded.scryfall_uri,
                    icon_svg_uri = excluded.icon_svg_uri,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    magic_set.set_id,
                    magic_set.code,
                    magic_set.name,
                    magic_set.set_type,
                    _date_to_str(magic_set.released_at),
                    magic_set.scryfall_uri,
                    magic_set.icon_svg_uri,
                    _date_to_str(magic_set.observed_at),
                ),
            )
            await db.commit()

    async def list_sets(self) -> List[SetState]:
        async with aiosqlite.connect(self._sqlite_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT
                    set_id,
                    code,
                    name,
                    set_type,
                    released_at,
                    scryfall_uri,
                    icon_svg_uri,
                    observed_at,
                    notified_announcement,
                    notified_t_minus_30,
                    notified_t_minus_14,
                    notified_t_minus_7,
                    notified_t_minus_1,
                    notified_release_day
                FROM mtg_sets
                """
            )
            rows = await cursor.fetchall()
        return [_row_to_state(row) for row in rows]

    async def mark_notified(self, set_id: str, milestone: SetMilestone) -> None:
        column = _milestone_column(milestone)
        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(
                f"""
                UPDATE mtg_sets
                SET {column} = 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE set_id = ?
                """,
                (set_id,),
            )
            await db.commit()


def _row_to_state(row: aiosqlite.Row) -> SetState:
    magic_set = MagicSet(
        set_id=row["set_id"],
        code=row["code"],
        name=row["name"],
        set_type=row["set_type"],
        released_at=_str_to_date(row["released_at"]),
        scryfall_uri=row["scryfall_uri"],
        icon_svg_uri=row["icon_svg_uri"],
        observed_at=_str_to_date(row["observed_at"]) or date.today(),
    )
    return SetState(
        magic_set=magic_set,
        notified_announcement=bool(row["notified_announcement"]),
        notified_t_minus_30=bool(row["notified_t_minus_30"]),
        notified_t_minus_14=bool(row["notified_t_minus_14"]),
        notified_t_minus_7=bool(row["notified_t_minus_7"]),
        notified_t_minus_1=bool(row["notified_t_minus_1"]),
        notified_release_day=bool(row["notified_release_day"]),
    )


def _milestone_column(milestone: SetMilestone) -> str:
    mapping = {
        SetMilestone.ANNOUNCEMENT: "notified_announcement",
        SetMilestone.T_MINUS_30: "notified_t_minus_30",
        SetMilestone.T_MINUS_14: "notified_t_minus_14",
        SetMilestone.T_MINUS_7: "notified_t_minus_7",
        SetMilestone.T_MINUS_1: "notified_t_minus_1",
        SetMilestone.RELEASE_DAY: "notified_release_day",
    }
    return mapping[milestone]


def _date_to_str(value: date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _str_to_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
