"""Configuration helpers for the MTG bot project."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional


def _getenv(key: str, default: Optional[str] = None) -> Optional[str]:
    """Fetch environment variables while trimming whitespace."""
    value = os.getenv(key, default)
    if value is None:
        return None
    return value.strip() or default


@dataclass
class DiscordSettings:
    token: str
    application_id: Optional[int]
    guild_id: Optional[int]
    release_channel_id: Optional[int]


@dataclass
class DatabaseSettings:
    postgres_dsn: Optional[str]
    redis_dsn: Optional[str]
    sqlite_path: str


@dataclass
class PollingSettings:
    default_interval_seconds: int = 300
    max_tasks_per_store: int = 10


@dataclass
class Settings:
    discord: DiscordSettings
    database: DatabaseSettings
    polling: PollingSettings
    vendors: "VendorSettings"
    schedule: "ScheduleSettings"
    environment: str = "development"


@dataclass
class VendorSettings:
    phoenix_store_feeds: List[str]
    big_box_urls: List[str]
    tcgplayer_public_key: Optional[str]
    tcgplayer_private_key: Optional[str]
    tcgplayer_skus: List[str]
    tcgplayer_cookie: Optional[str]


@dataclass
class ScheduleSettings:
    scryfall_poll_interval_minutes: int = 720
    digest_hour_utc: int = 15
    digest_minute_utc: int = 0


def load_settings() -> Settings:
    """Construct a Settings object from environment variables."""
    # Lazy import so running without python-dotenv remains possible.
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ModuleNotFoundError:
        pass

    discord = DiscordSettings(
        token=_getenv("DISCORD_BOT_TOKEN", "") or "",
        application_id=(
            int(_getenv("DISCORD_APPLICATION_ID", "0") or "0") or None
        ),
        guild_id=int(_getenv("DISCORD_GUILD_ID", "0") or "0") or None,
        release_channel_id=int(
            _getenv("DISCORD_RELEASE_CHANNEL_ID", "0") or "0"
        )
        or None,
    )

    database = DatabaseSettings(
        postgres_dsn=_getenv("POSTGRES_DSN"),
        redis_dsn=_getenv("REDIS_DSN"),
        sqlite_path=_getenv("SQLITE_DB_PATH", "data/mtgbot.db") or "data/mtgbot.db",
    )

    polling = PollingSettings(
        default_interval_seconds=int(
            _getenv("DEFAULT_POLL_INTERVAL_SECONDS", "300") or "300"
        ),
        max_tasks_per_store=int(
            _getenv("MAX_TASKS_PER_STORE", "10") or "10"
        ),
    )

    vendors = VendorSettings(
        phoenix_store_feeds=_split_list(_getenv("PHOENIX_STORE_FEEDS", "")),
        big_box_urls=_split_list(_getenv("BIG_BOX_PRODUCT_URLS", "")),
        tcgplayer_public_key=_getenv("TCGPLAYER_PUBLIC_KEY"),
        tcgplayer_private_key=_getenv("TCGPLAYER_PRIVATE_KEY"),
        tcgplayer_skus=_split_list(_getenv("TCGPLAYER_SKUS", "")),
        tcgplayer_cookie=_getenv("TCGPLAYER_COOKIE"),
    )

    schedule = ScheduleSettings(
        scryfall_poll_interval_minutes=int(
            _getenv("SCRYFALL_POLL_INTERVAL_MINUTES", "720") or "720"
        ),
        digest_hour_utc=int(_getenv("DIGEST_HOUR_UTC", "15") or "15"),
        digest_minute_utc=int(_getenv("DIGEST_MINUTE_UTC", "0") or "0"),
    )

    return Settings(
        discord=discord,
        database=database,
        polling=polling,
        vendors=vendors,
        schedule=schedule,
        environment=_getenv("APP_ENV", "development") or "development",
    )


__all__ = [
    "DatabaseSettings",
    "DiscordSettings",
    "PollingSettings",
    "Settings",
    "VendorSettings",
    "ScheduleSettings",
    "load_settings",
]


def _split_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]
