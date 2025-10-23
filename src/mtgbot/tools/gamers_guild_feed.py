"""Transform Gamers Guild AZ Shopify feed into Phoenix watcher JSON schema.

Usage:
    poetry run python -m mtgbot.tools.gamers_guild_feed --once
    poetry run python -m mtgbot.tools.gamers_guild_feed --serve --port 8081
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp
from aiohttp import web

log = logging.getLogger(__name__)

BASE_URL = "https://gamersguildaz.com"
COLLECTION_PATH = "/collections/new-arrivals/products.json"


@dataclass(slots=True)
class Product:
    product_id: str
    name: str
    price: float
    available: bool
    url: str
    tags: List[str]
    image: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.product_id,
            "name": self.name,
            "price": self.price,
            "available": self.available,
            "url": self.url,
            "tags": self.tags,
            "image": self.image,
        }


async def fetch_products(session: aiohttp.ClientSession) -> List[Product]:
    products: List[Product] = []
    page = 1
    while True:
        params = {"page": page, "limit": 250}
        url = f"{BASE_URL}{COLLECTION_PATH}"
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                log.warning("Gamers Guild feed returned HTTP %s", resp.status)
                break
            payload = await resp.json()
        raw_products: List[Dict[str, Any]] = payload.get("products", [])
        if not raw_products:
            break
        for raw in raw_products:
            product = _normalize_product(raw)
            if product:
                products.append(product)
        page += 1
    return products


def _normalize_product(raw: Dict[str, Any]) -> Optional[Product]:
    title = raw.get("title") or ""
    tags = [tag.strip() for tag in (raw.get("tags") or "").split(",") if tag.strip()]
    product_type = (raw.get("product_type") or "").lower()
    if "magic" not in product_type and not any("magic" in tag.lower() for tag in tags):
        # Skip non-MTG items.
        return None

    variants = raw.get("variants") or []
    if not variants:
        return None
    variant = variants[0]
    price = float(variant.get("price") or 0.0)
    available = bool(variant.get("available", False))
    sku = variant.get("sku") or raw.get("handle") or raw.get("id")
    product_url = f"{BASE_URL}/products/{raw.get('handle') or sku}"
    image = None
    images = raw.get("images") or []
    if images:
        image = images[0].get("src")
    return Product(
        product_id=str(raw.get("id")),
        name=title,
        price=price,
        available=available,
        url=product_url,
        tags=tags,
        image=image,
    )


def build_payload(products: List[Product]) -> Dict[str, Any]:
    return {
        "store": "Gamers Guild AZ",
        "source": f"{BASE_URL}{COLLECTION_PATH}",
        "contact_url": f"{BASE_URL}/pages/contact-us",
        "products": [product.to_dict() for product in products],
    }


async def run_once() -> None:
    async with aiohttp.ClientSession() as session:
        products = await fetch_products(session)
    payload = build_payload(products)
    print(json.dumps(payload, indent=2))


async def serve(host: str, port: int, interval: int) -> None:
    cache: Dict[str, Any] = {"payload": build_payload([])}

    async def refresh_loop() -> None:
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    products = await fetch_products(session)
                    cache["payload"] = build_payload(products)
                    log.info("Fetched %d Gamers Guild products", len(products))
                except Exception as exc:  # noqa: BLE001
                    log.exception("Failed to refresh Gamers Guild feed: %s", exc)
                await asyncio.sleep(max(interval, 60))

    async def handle(request: web.Request) -> web.Response:
        return web.json_response(cache["payload"])

    app = web.Application()
    app.router.add_get("/", handle)
    app.router.add_get("/feed", handle)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    log.info("Serving Gamers Guild feed on http://%s:%s/feed", host, port)

    try:
        await refresh_loop()
    finally:
        await runner.cleanup()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gamers Guild AZ feed transformer")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Fetch once and print JSON to stdout",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Run an HTTP server that refreshes periodically",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Server host (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8081, help="Server port (default 8081)")
    parser.add_argument(
        "--interval",
        type=int,
        default=900,
        help="Refresh interval in seconds when serving (default 900)",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = parse_args()
    if args.once:
        asyncio.run(run_once())
        return
    if args.serve:
        asyncio.run(serve(args.host, args.port, args.interval))
        return
    print("Specify --once or --serve", flush=True)


if __name__ == "__main__":
    main()
