"""SQLite storage for TCGplayer cart credentials."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import aiosqlite


@dataclass(slots=True)
class CartCredentials:
    discord_user_id: int
    cookie: str
    cart_key: str
    updated_at: datetime


class TcgplayerCartRepository:
    def __init__(self, sqlite_path: str) -> None:
        self._sqlite_path = sqlite_path

    async def init(self) -> None:
        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tcg_cart_credentials (
                    discord_user_id INTEGER PRIMARY KEY,
                    cookie TEXT NOT NULL,
                    cart_key TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.commit()

    async def upsert_credentials(
        self, discord_user_id: int, cookie: str, cart_key: str
    ) -> None:
        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(
                """
                INSERT INTO tcg_cart_credentials (discord_user_id, cookie, cart_key)
                VALUES (?, ?, ?)
                ON CONFLICT(discord_user_id) DO UPDATE SET
                    cookie = excluded.cookie,
                    cart_key = excluded.cart_key,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (discord_user_id, cookie, cart_key),
            )
            await db.commit()

    async def remove_credentials(self, discord_user_id: int) -> bool:
        async with aiosqlite.connect(self._sqlite_path) as db:
            cursor = await db.execute(
                "DELETE FROM tcg_cart_credentials WHERE discord_user_id = ?",
                (discord_user_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def fetch_credentials(
        self, discord_user_id: int
    ) -> Optional[CartCredentials]:
        async with aiosqlite.connect(self._sqlite_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT discord_user_id, cookie, cart_key, updated_at
                FROM tcg_cart_credentials
                WHERE discord_user_id = ?
                """,
                (discord_user_id,),
            )
            row = await cursor.fetchone()
        if not row:
            return None
        return CartCredentials(
            discord_user_id=row["discord_user_id"],
            cookie=row["cookie"],
            cart_key=row["cart_key"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


__all__ = ["CartCredentials", "TcgplayerCartRepository"]
