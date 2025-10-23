"""SQLite storage for TCGplayer sales history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List

import aiosqlite


@dataclass(slots=True)
class SaleRecord:
    tcg_id: int
    title: str
    order_datetime: datetime
    quantity: int
    price: float


class TcgplayerSalesRepository:
    def __init__(self, sqlite_path: str) -> None:
        self._sqlite_path = sqlite_path

    async def init(self) -> None:
        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tcg_sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tcg_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    order_date TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(tcg_id, order_date, quantity, price)
                )
                """
            )
            await db.commit()

    async def upsert_sales(self, records: Iterable[SaleRecord]) -> None:
        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.executemany(
                """
                INSERT OR IGNORE INTO tcg_sales (
                    tcg_id,
                    title,
                    order_date,
                    quantity,
                    price
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        record.tcg_id,
                        record.title,
                        record.order_datetime.isoformat(),
                        record.quantity,
                        record.price,
                    )
                    for record in records
                ],
            )
            await db.commit()

    async def list_sales(
        self,
        tcg_id: int,
        *,
        within_days: int = 90,
        limit: int | None = None,
    ) -> List[SaleRecord]:
        cutoff = datetime.utcnow() - timedelta(days=max(within_days, 1))
        query = (
            "SELECT title, order_date, quantity, price FROM tcg_sales "
            "WHERE tcg_id = ? AND order_date >= ? "
            "ORDER BY order_date DESC"
        )
        params: list[object] = [tcg_id, cutoff.isoformat()]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        async with aiosqlite.connect(self._sqlite_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

        records = [
            SaleRecord(
                tcg_id=tcg_id,
                title=row["title"],
                order_datetime=datetime.fromisoformat(row["order_date"]),
                quantity=row["quantity"],
                price=row["price"],
            )
            for row in rows
        ]
        return records


__all__ = ["SaleRecord", "TcgplayerSalesRepository"]
