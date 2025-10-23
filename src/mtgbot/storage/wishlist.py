"""SQLite-backed repositories for wishlists and Discord role mappings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Optional

import aiosqlite

from mtgbot.models import ActionType, CardSku, RoleMapping, WishlistEntry, Vendor


def _ensure_directory(sqlite_path: str) -> None:
    Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)


def _encode_tags(tags: Iterable[str]) -> str:
    return json.dumps(list(tags))


def _decode_tags(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [part.strip() for part in raw.split(",") if part.strip()]


def _encode_vendors(vendors: Iterable[Vendor]) -> str:
    return json.dumps([vendor.value for vendor in vendors])


def _decode_vendors(raw: Optional[str]) -> List[Vendor]:
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        values = [part.strip() for part in raw.split(",") if part.strip()]
    result: List[Vendor] = []
    for value in values:
        try:
            result.append(Vendor(value))
        except ValueError:
            continue
    return result


class WishlistRepository:
    """Persistence layer for wishlist entries."""

    def __init__(self, sqlite_path: str) -> None:
        self._sqlite_path = sqlite_path

    async def init(self) -> None:
        _ensure_directory(self._sqlite_path)
        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS wishlist_entries (
                    discord_user_id INTEGER NOT NULL,
                    oracle_id TEXT NOT NULL,
                    product_code TEXT,
                    finish TEXT,
                    collector_number TEXT,
                    set_code TEXT,
                    vendor_sku TEXT,
                    max_price REAL,
                    action TEXT NOT NULL,
                    tags TEXT DEFAULT '[]',
                    preferred_vendors TEXT DEFAULT '[]',
                    UNIQUE(discord_user_id, oracle_id)
                )
                """
            )
            await db.commit()

    async def upsert_entry(self, entry: WishlistEntry) -> None:
        sku = entry.sku
        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(
                """
                INSERT INTO wishlist_entries (
                    discord_user_id,
                    oracle_id,
                    product_code,
                    finish,
                    collector_number,
                    set_code,
                    vendor_sku,
                    max_price,
                    action,
                    tags,
                    preferred_vendors
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(discord_user_id, oracle_id) DO UPDATE SET
                    product_code=excluded.product_code,
                    finish=excluded.finish,
                    collector_number=excluded.collector_number,
                    set_code=excluded.set_code,
                    vendor_sku=excluded.vendor_sku,
                    max_price=excluded.max_price,
                    action=excluded.action,
                    tags=excluded.tags,
                    preferred_vendors=excluded.preferred_vendors
                """,
                (
                    entry.discord_user_id,
                    sku.oracle_id,
                    sku.product_code,
                    sku.finish,
                    sku.collector_number,
                    sku.set_code,
                    sku.vendor_sku,
                    entry.max_price,
                    entry.action_preference.value,
                    _encode_tags(entry.tags),
                    _encode_vendors(entry.preferred_vendors),
                ),
            )
            await db.commit()

    async def remove_entry(self, discord_user_id: int, oracle_id: str) -> bool:
        async with aiosqlite.connect(self._sqlite_path) as db:
            cursor = await db.execute(
                """
                DELETE FROM wishlist_entries
                WHERE discord_user_id = ? AND oracle_id = ?
                """,
                (discord_user_id, oracle_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def list_entries_for_user(self, discord_user_id: int) -> List[WishlistEntry]:
        async with aiosqlite.connect(self._sqlite_path) as db:
            cursor = await db.execute(
                """
                SELECT discord_user_id,
                       oracle_id,
                       product_code,
                       finish,
                       collector_number,
                       set_code,
                       vendor_sku,
                       max_price,
                       action,
                       tags,
                       preferred_vendors
                FROM wishlist_entries
                WHERE discord_user_id = ?
                ORDER BY oracle_id
                """,
                (discord_user_id,),
            )
            rows = await cursor.fetchall()
        return [_row_to_entry(row) for row in rows]

    async def list_all_entries(self) -> List[WishlistEntry]:
        async with aiosqlite.connect(self._sqlite_path) as db:
            cursor = await db.execute(
                """
                SELECT discord_user_id,
                       oracle_id,
                       product_code,
                       finish,
                       collector_number,
                       set_code,
                       vendor_sku,
                       max_price,
                       action,
                       tags,
                       preferred_vendors
                FROM wishlist_entries
                """
            )
            rows = await cursor.fetchall()
        return [_row_to_entry(row) for row in rows]


class RoleMappingRepository:
    """Persistence for tag -> Discord role ID mappings."""

    def __init__(self, sqlite_path: str) -> None:
        self._sqlite_path = sqlite_path

    async def init(self) -> None:
        _ensure_directory(self._sqlite_path)
        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS role_mappings (
                    tag TEXT PRIMARY KEY,
                    role_id INTEGER NOT NULL
                )
                """
            )
            await db.commit()

    async def upsert_mapping(self, mapping: RoleMapping) -> None:
        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(
                """
                INSERT INTO role_mappings (tag, role_id)
                VALUES (?, ?)
                ON CONFLICT(tag) DO UPDATE SET
                    role_id = excluded.role_id
                """,
                (mapping.tag, mapping.role_id),
            )
            await db.commit()

    async def remove_mapping(self, tag: str) -> bool:
        async with aiosqlite.connect(self._sqlite_path) as db:
            cursor = await db.execute(
                "DELETE FROM role_mappings WHERE tag = ?", (tag,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def list_mappings(self) -> List[RoleMapping]:
        async with aiosqlite.connect(self._sqlite_path) as db:
            cursor = await db.execute(
                "SELECT tag, role_id FROM role_mappings ORDER BY tag"
            )
            rows = await cursor.fetchall()
        return [RoleMapping(tag=row[0], role_id=row[1]) for row in rows]


def _row_to_entry(row: aiosqlite.Row) -> WishlistEntry:
    discord_user_id = int(row[0])
    sku = CardSku(
        oracle_id=row[1],
        product_code=row[2] or row[1],
        finish=row[3] or "any",
        collector_number=row[4],
        set_code=row[5],
        vendor_sku=row[6],
    )
    max_price = row[7]
    action = ActionType(row[8])
    tags = _decode_tags(row[9])
    preferred_vendors = _decode_vendors(row[10])
    return WishlistEntry(
        discord_user_id=discord_user_id,
        sku=sku,
        max_price=max_price,
        action_preference=action,
        tags=tags,
        preferred_vendors=preferred_vendors,
    )
