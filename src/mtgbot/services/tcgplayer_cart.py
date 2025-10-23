"""Service for managing TCGplayer cart automation."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import aiohttp

from mtgbot.storage.tcgplayer_cart import (
    CartCredentials,
    TcgplayerCartRepository,
)

log = logging.getLogger(__name__)

_CART_KEY_PATTERN = re.compile(r"StoreCart_PRODUCTION=CK=([^&;]+)")


@dataclass(slots=True)
class CartResult:
    added: bool
    subtotal: Optional[float]
    message: str
    summary: Optional[dict]


class TcgplayerCartService:
    BASE_URL = "https://mpgateway.tcgplayer.com/v1"
    DEFAULT_MPFEV = "4426"

    def __init__(self, repository: TcgplayerCartRepository) -> None:
        self._repo = repository
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()

    def set_session(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def connect(self, discord_user_id: int, cookie: str) -> str:
        """Store cookie and derived cart key for user."""
        cart_key = self._extract_cart_key(cookie)
        await self._repo.upsert_credentials(discord_user_id, cookie, cart_key)
        return cart_key

    async def disconnect(self, discord_user_id: int) -> bool:
        return await self._repo.remove_credentials(discord_user_id)

    async def has_credentials(self, discord_user_id: int) -> bool:
        creds = await self._repo.fetch_credentials(discord_user_id)
        return creds is not None

    async def add_item(
        self,
        discord_user_id: int,
        *,
        sku: int,
        seller_key: str,
        quantity: int,
        price: float,
        is_direct: bool,
        channel_id: int = 0,
        country_code: str = "US",
    ) -> CartResult:
        session = self._session
        if session is None:
            raise RuntimeError("HTTP session not available")

        creds = await self._repo.fetch_credentials(discord_user_id)
        if not creds:
            return CartResult(
                added=False,
                subtotal=None,
                message="No TCGplayer credentials stored. Use /tcgplayer connect first.",
                summary=None,
            )

        headers = {
            "Content-Type": "application/json",
            "Origin": "https://www.tcgplayer.com",
            "Referer": "https://www.tcgplayer.com/",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/141.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Cookie": creds.cookie,
        }

        url = (
            f"{self.BASE_URL}/cart/{creds.cart_key}/item/add"
            f"?mpfev={self.DEFAULT_MPFEV}"
        )
        payload = {
            "sku": sku,
            "sellerKey": seller_key,
            "channelId": channel_id,
            "requestedQuantity": quantity,
            "price": price,
            "isDirect": is_direct,
            "countryCode": country_code,
        }

        async with self._lock:
            try:
                async with session.post(url, headers=headers, json=payload) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        log.warning("Add to cart failed (%s): %s", resp.status, text)
                        return CartResult(
                            added=False,
                            subtotal=None,
                            message=f"TCGplayer returned HTTP {resp.status}",
                            summary=None,
                        )
                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError:
                        data = {"raw": text}
            except aiohttp.ClientError as exc:
                log.exception("TCGplayer cart request failed: %s", exc)
                return CartResult(
                    added=False,
                    subtotal=None,
                    message="Cart request failed; see logs for details.",
                    summary=None,
                )

            summary = await self._fetch_summary(session, creds, headers)
            subtotal = None
            if summary and isinstance(summary, dict):
                subtotal = _dig_float(summary, ["summary", "subtotalAmount"])
            return CartResult(
                added=True,
                subtotal=subtotal,
                message="Item added to cart.",
                summary=summary or data,
            )

    async def _fetch_summary(
        self,
        session: aiohttp.ClientSession,
        creds: CartCredentials,
        headers: dict[str, str],
    ) -> Optional[dict]:
        url = (
            f"{self.BASE_URL}/cart/{creds.cart_key}/summary"
            f"?includeTaxes=false&mpfev={self.DEFAULT_MPFEV}"
        )
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    log.warning("Cart summary failed (%s)", resp.status)
                    return None
                return await resp.json()
        except aiohttp.ClientError as exc:
            log.warning("Cart summary request failed: %s", exc)
            return None

    def _extract_cart_key(self, cookie: str) -> str:
        match = _CART_KEY_PATTERN.search(cookie)
        if not match:
            raise ValueError(
                "Cookie missing StoreCart_PRODUCTION=CK=... entry; copy the cookie string from the cart page."
            )
        return match.group(1)


def _dig_float(data: dict, path: list[str]) -> Optional[float]:
    current: object = data
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    try:
        return float(current)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


__all__ = [
    "TcgplayerCartService",
    "CartResult",
]
