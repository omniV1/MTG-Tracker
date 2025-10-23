# MTG Release Automation Bot (Phoenix Focus)

Discord-first scaffold for monitoring Magic: The Gathering product drops, notifying Phoenix-area players, and optionally staging carts with approved retailers.

## Project Goals
- Track preorder and release availability for paper singles, sealed product, and optional MTGO items.
- Route high-signal events into Discord channels/DMs with wishlist-aware tagging.
- Respect retailer terms of service; fall back to notifications when automated carting is prohibited.
- Support Phoenix local store alerts (newsletters/Discord) alongside national online retailers.

## Architecture Overview
- **Watchers** poll vendor feeds, APIs, or newsletters and emit normalized `InventoryEvent` objects.
- **Decision Engine** matches events against wishlist entries (price caps, tags, Phoenix pickup preference) and produces `Decision` actions.
- **Discord Bot** delivers notifications, provides slash commands for wishlist management, and later orchestrates cart placement confirmations.
- **Persistence Layer (planned)** PostgreSQL for card metadata & user preferences, Redis for rate limiting/idempotency, Vault/KMS for session secrets.

## Getting Started
1. Install dependencies  
   ```bash
   poetry install
   ```
2. Copy environment defaults  
   ```bash
   cp .env.example .env
   ```
   Fill in `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, and `DISCORD_RELEASE_CHANNEL_ID`.
3. Run database migrations (SQLite is created automatically)  
   ```bash
   mkdir -p data
   ```
4. (Optional) Adjust `.env` scheduling knobs (`SCRYFALL_POLL_INTERVAL_MINUTES`, `DIGEST_HOUR_UTC`, `DIGEST_MINUTE_UTC`).
5. Run the scaffold  
   ```bash
   poetry run python -m mtgbot.bot
   ```
   The Scryfall set watcher runs immediately; Card Kingdom/premium feeds trigger when their endpoints respond.

## Key Modules
- `mtgbot.bot` – orchestrates watchers, decision engine, scheduling loops, and Discord client lifecycle.
- `mtgbot.notifications.discord_bot` – Discord embed formatting, slash commands, and message dispatch.
- `mtgbot.watchers.card_kingdom.CardKingdomWatcher` – scrapes preorder listings with dedupe.
- `mtgbot.watchers.scryfall_sets.ScryfallSetWatcher` – polls Scryfall for new/updated set metadata.
- `mtgbot.watchers.local_store.PhoenixLocalStoreWatcher` – consumes Phoenix LGS JSON feeds.
- `mtgbot.watchers.tcgplayer.TcgplayerWatcher` – polls the official TCGplayer pricing API.
- `mtgbot.watchers.big_box.BigBoxWatcher` – heuristically checks Amazon/Target/Best Buy/Walmart product pages.
- `mtgbot.services.wishlist.WishlistService` – persists wishlists/role mappings into SQLite and feeds the rules engine.
- `mtgbot.services.set_schedule.SetScheduleService` – stores set timelines, raises milestone alerts, and assembles digests.

## Discord Bot Commands
- `/wishlist add identifier:<slug> action:<notify|cart> [max_price] [tags] [preferred_vendors]`
- `/wishlist list`
- `/wishlist remove identifier:<slug>`
- `/roles map tag:<name> role:<@role>` *(requires Manage Server)*
- `/roles unmap tag:<name>`
- `/roles list`
- `/alert_test` *(Manage Server only)* — pushes a sample set alert to verify embeds/buttons.
- `/sets upcoming [days]` — on-demand digest of releases in the next `days` (default 90, max 365).

Tags tie into Discord role mentions so Phoenix pickup groups can be pinged without spamming everyone.

## Phoenix Feed Example: Gamers Guild AZ
1. Run the transformer locally:  
   ```bash
   poetry run python -m mtgbot.tools.gamers_guild_feed --serve --port 8081
   ```
   This hosts `http://localhost:8081/feed` returning the normalized Phoenix feed JSON.
2. Add the feed URL to `.env` (comma-separate if you have multiple feeds):  
   ```env
   PHOENIX_STORE_FEEDS=http://localhost:8081/feed
   ```
3. Restart the bot so `PhoenixLocalStoreWatcher` begins emitting Gamers Guild alerts. Use `--once` instead of `--serve` if you just want to dump the feed output.
   When a product surfaces, the bot adds a "Contact Store" button pointing at the store's site/Discord.

## TCGplayer Sales Snapshots
- `/tcg_sales product_id:<id> [days] [max_listings]` plots the latest TCGplayer sales for the given product ID and posts a chart with stats. Defaults: 90 days, 100 records.  
- Supply `TCGPLAYER_PUBLIC_KEY` and `TCGPLAYER_PRIVATE_KEY` in `.env` for authenticated requests; without keys the bot falls back to the public marketplace endpoint.  
- Example: `/tcg_sales 544234 60` shows 60 days of Extended Art Final Showdown sales.

## TCGplayer Cart Automation (experimental)
- `/tcgplayer connect cookie:<string>` securely stores your TCGplayer cookie (must include `StoreCart_PRODUCTION=CK=`). Paste it from the browser’s cart page.
- `/tcgplayer status` confirms whether credentials are stored; `/tcgplayer disconnect` removes them.
- `/tcgplayer cart_add sku:<id> seller_key:<key> price:<amt> [quantity] [is_direct] [channel_id] [country_code]` replays the browser request to add an item to your cart.
- The bot requires your cookie and mimics browser headers. Cookies expire; re-run `/tcgplayer connect` whenever you refresh the browser session.
- Always obtain TCGplayer’s approval before enabling auto-cart in production.

## Roadmap
1. **Phoenix Retailer Feeds** – wire store newsletters or Discord feeds to the JSON format expected by `PhoenixLocalStoreWatcher`.
2. **SKU Normalization** – connect Scryfall/MTGJSON lookups to replace slug identifiers with canonical oracle IDs.
3. **TCGplayer Coverage** – expand SKU whitelist management (UI/commands) and add product discovery beyond hard-coded SKUs.
4. **Cart Automation** – integrate TCGplayer Pro/Card Kingdom APIs with encrypted user tokens, reconciliation checks, and opt-in commands.
5. **Dashboard/API (Optional)** – small FastAPI admin surface for health checks, connector toggles, and audit logs.

## Development Notes
- Respect per-vendor rate limits; store last-seen hashes to avoid spamming Discord on unchanged listings.
- Add unit tests for each watcher and decision rule once real data sources are integrated.
- Phoenix feed schema: each URL should return JSON `{"store": "Name", "products": [{"id": "sku", "name": "Collector Booster", "price": 239.99, "available": true, "url": "https://store"}]}`.
- Use `ruff` for linting (`poetry run ruff check src`) and `pytest` for future tests.
- Sensitive automation (cart placement) must log every action and support rapid disable via Discord admin command.
## Set Timeline Alerts
- The bot polls Scryfall every `SCRYFALL_POLL_INTERVAL_MINUTES` (min 30) and persists set metadata in SQLite.
- It emits milestone alerts (announcement, 30/14/7/1-day warnings, release day) directly to the configured Discord channel.
- A daily digest (UTC `DIGEST_HOUR_UTC`:`DIGEST_MINUTE_UTC`) summarizes releases coming up in the next 90 days.
