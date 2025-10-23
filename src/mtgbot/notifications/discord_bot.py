"""Discord bot entrypoint and notification helpers."""

from __future__ import annotations

import logging
from datetime import date
from typing import Iterable, Optional, Literal, Sequence

import discord
from discord import app_commands
from urllib.parse import quote_plus

from mtgbot.config import Settings
from mtgbot.models import (
    ActionType,
    Decision,
    ListingSnapshot,
    MagicSet,
    SetAlert,
    SetMilestone,
    WishlistEntry,
    Vendor,
)
from mtgbot.services.wishlist import WishlistService
from mtgbot.services.set_schedule import SetScheduleService, UpcomingSet
from mtgbot.services.tcgplayer_sales import (
    SalesSummary,
    TcgSalesError,
    TcgplayerSalesService,
)

log = logging.getLogger(__name__)
_TEST_MILESTONE = SetMilestone.ANNOUNCEMENT


class MtgDiscordBot(discord.Client):
    def __init__(
        self,
        settings: Settings,
        wishlist_service: WishlistService,
        set_schedule_service: Optional[SetScheduleService] = None,
        tcgplayer_sales_service: Optional[TcgplayerSalesService] = None,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = False
        super().__init__(intents=intents)
        self.settings = settings
        self.wishlist_service = wishlist_service
        self.set_schedule_service = set_schedule_service
        self.tcgplayer_sales_service = tcgplayer_sales_service
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await self.tree.sync(guild=self._guild)

    @property
    def _guild(self) -> Optional[discord.Object]:
        if not self.settings.discord.guild_id:
            return None
        return discord.Object(id=self.settings.discord.guild_id)

    async def send_decisions(
        self, decisions: Iterable[Decision]
    ) -> list[discord.Message]:
        await self.wait_until_ready()
        channel_id = self.settings.discord.release_channel_id
        if not channel_id:
            log.warning("Release channel ID missing; skipping Discord send")
            return []

        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)

        messages: list[discord.Message] = []
        for decision in decisions:
            embed = _build_embed(decision.event.snapshot, decision)
            mention = self.wishlist_service.resolve_role_mentions(
                decision.wishlist.tags
            )
            content = f"{mention} New availability alert" if mention else None
            view = _decision_link_view(decision)
            messages.append(
                await channel.send(content=content, embed=embed, view=view)
            )
        return messages

    async def send_set_alerts(self, alerts: Sequence[SetAlert]) -> list[discord.Message]:
        await self.wait_until_ready()
        channel_id = self.settings.discord.release_channel_id
        if not channel_id or not alerts:
            return []
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        messages: list[discord.Message] = []
        for alert in alerts:
            embed = _set_alert_embed(alert)
            view = _magic_set_link_view(alert.magic_set)
            messages.append(await channel.send(embed=embed, view=view))
        return messages

    async def send_set_digest(
        self,
        upcoming_sets: Sequence[UpcomingSet],
        *,
        title: str = "Upcoming MTG Releases",
    ) -> Optional[discord.Message]:
        await self.wait_until_ready()
        channel_id = self.settings.discord.release_channel_id
        if not channel_id:
            return None
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        embed = _set_digest_embed(upcoming_sets, title=title)
        return await channel.send(embed=embed)

    def register_commands(self) -> None:
        tree = self.tree

        @tree.command(name="ping", description="Check bot latency")
        async def _ping(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(
                f"Pong! {round(self.latency * 1000)} ms"
            )

        wishlist_group = app_commands.Group(
            name="wishlist", description="Manage wishlist entries"
        )

        @wishlist_group.command(name="add")
        @app_commands.describe(
            identifier="Card or product identifier (Scryfall oracle ID, SKU slug, etc.)",
            action="notify users or attempt to stage a cart add",
            max_price="Maximum acceptable price in USD",
            tags="Comma-separated tags to drive Discord role mentions",
            preferred_vendors="Comma-separated vendor ids (e.g., card_kingdom, tcgplayer)",
        )
        async def wishlist_add(
            interaction: discord.Interaction,
            identifier: str,
            action: Literal["notify", "cart"] = "notify",
            max_price: Optional[float] = None,
            tags: Optional[str] = None,
            preferred_vendors: Optional[str] = None,
        ) -> None:
            await interaction.response.defer(ephemeral=True)
            entry = await self.wishlist_service.add_or_update_entry(
                interaction.user.id,
                identifier.strip(),
                max_price=max_price,
                action=ActionType(action),
                tags=tags,
                preferred_vendors=preferred_vendors,
            )
            await interaction.followup.send(
                f"Stored wishlist entry `{entry.sku.oracle_id}` with action `{entry.action_preference.value}`.",
                ephemeral=True,
            )

        @wishlist_group.command(name="list")
        async def wishlist_list(
            interaction: discord.Interaction,
        ) -> None:
            await interaction.response.defer(ephemeral=True)
            entries = await self.wishlist_service.list_entries(
                interaction.user.id
            )
            if not entries:
                await interaction.followup.send(
                    "Your wishlist is empty.", ephemeral=True
                )
                return
            lines = [
                _format_wishlist_entry(entry) for entry in entries
            ]
            message = "\n".join(lines)
            await interaction.followup.send(message[:1900], ephemeral=True)

        @wishlist_group.command(name="remove")
        @app_commands.describe(
            identifier="Identifier previously used with /wishlist add"
        )
        async def wishlist_remove(
            interaction: discord.Interaction, identifier: str
        ) -> None:
            await interaction.response.defer(ephemeral=True)
            removed = await self.wishlist_service.remove_entry(
                interaction.user.id, identifier.strip()
            )
            if removed:
                await interaction.followup.send(
                    f"Removed wishlist entry `{identifier}`.", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"No wishlist entry found for `{identifier}`.",
                    ephemeral=True,
                )

        tree.add_command(wishlist_group, guild=self._guild)

        roles_group = app_commands.Group(
            name="roles",
            description="Configure Discord role mentions for wishlist tags",
            default_permissions=discord.Permissions(manage_guild=True),
        )

        @roles_group.command(name="map")
        @app_commands.describe(
            tag="Wishlist tag to associate",
            role="Discord role to mention when alerts include this tag",
        )
        @app_commands.guild_only()
        async def roles_map(
            interaction: discord.Interaction, tag: str, role: discord.Role
        ) -> None:
            await interaction.response.defer(ephemeral=True)
            mapping = await self.wishlist_service.set_role_mapping(
                tag.strip(), role.id
            )
            await interaction.followup.send(
                f"Linked tag `{mapping.tag}` to role {role.mention}.",
                ephemeral=True,
            )

        @roles_group.command(name="unmap")
        @app_commands.describe(tag="Wishlist tag to unlink from any role")
        @app_commands.guild_only()
        async def roles_unmap(
            interaction: discord.Interaction, tag: str
        ) -> None:
            await interaction.response.defer(ephemeral=True)
            removed = await self.wishlist_service.remove_role_mapping(
                tag.strip()
            )
            if removed:
                await interaction.followup.send(
                    f"Removed role mapping for `{tag}`.", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"No mapping found for `{tag}`.", ephemeral=True
                )

        @roles_group.command(name="list")
        @app_commands.guild_only()
        async def roles_list(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True)
            mappings = await self.wishlist_service.list_role_mappings()
            if not mappings:
                await interaction.followup.send(
                    "No role mappings configured.", ephemeral=True
                )
                return
            lines = [
                f"`{mapping.tag}` ⟶ <@&{mapping.role_id}>"
                for mapping in mappings
            ]
            await interaction.followup.send(
                "\n".join(lines)[:1900], ephemeral=True
            )

        tree.add_command(roles_group, guild=self._guild)

        sets_group = app_commands.Group(
            name="sets",
            description="MTG set timelines and releases",
        )

        @sets_group.command(name="upcoming", description="Show upcoming MTG releases")
        @app_commands.describe(days="Number of days ahead to include (default 90)")
        async def sets_upcoming(
            interaction: discord.Interaction, days: Optional[int] = None
        ) -> None:
            if not self.set_schedule_service:
                await interaction.response.send_message(
                    "Set schedule data is not available.",
                    ephemeral=True,
                )
                return
            window = days or 90
            window = max(1, min(window, 365))
            await interaction.response.defer(ephemeral=True)
            upcoming = await self.set_schedule_service.upcoming_sets(
                within_days=window
            )
            if not upcoming:
                await interaction.followup.send(
                    f"No sets release within the next {window} days.",
                    ephemeral=True,
                )
                return
            limit = min(len(upcoming), 5)
            for item in upcoming[:limit]:
                embed = _upcoming_set_embed(item)
                view = _magic_set_link_view(item.magic_set)
                await interaction.followup.send(embed=embed, view=view, ephemeral=False)
            if len(upcoming) > limit:
                await interaction.followup.send(
                    f"...and {len(upcoming) - limit} more set(s) beyond the first {limit}.",
                    ephemeral=True,
                )

        tree.add_command(sets_group, guild=self._guild)

        @tree.command(
            name="tcg_sales", description="Show recent TCGplayer sales history"
        )
        @app_commands.describe(
            product_id="TCGplayer product ID (numeric)",
            days="Number of days to include (default 90)",
            max_listings="Maximum sales records to fetch (default 100)",
        )
        async def tcg_sales(  # type: ignore[unused-ignore]
            interaction: discord.Interaction,
            product_id: int,
            days: Optional[int] = None,
            max_listings: Optional[int] = None,
        ) -> None:
            if not self.tcgplayer_sales_service:
                await interaction.response.send_message(
                    "TCGplayer sales service is not configured.",
                    ephemeral=True,
                )
                return

            window = max(7, min(days or 90, 365))
            limit = max(25, min(max_listings or 100, 200))

            await interaction.response.defer(thinking=True)

            try:
                records = await self.tcgplayer_sales_service.fetch_and_store_sales(
                    product_id,
                    max_listings=limit,
                    days=window,
                )
            except TcgSalesError as exc:
                await interaction.followup.send(
                    f"Failed to fetch sales data: {exc}", ephemeral=True
                )
                return

            if not records:
                await interaction.followup.send(
                    f"No sales found in the last {window} days.",
                    ephemeral=True,
                )
                return

            try:
                image, summary = await self.tcgplayer_sales_service.build_chart(
                    records
                )
            except TcgSalesError as exc:
                await interaction.followup.send(
                    f"Unable to build chart: {exc}", ephemeral=True
                )
                return

            avg_price = sum(r.price for r in records) / len(records)
            total_quantity = sum(r.quantity for r in records)

            direction = summary.gain
            color = discord.Color.green() if direction >= 0 else discord.Color.red()
            file_name = f"tcg_sales_{summary.tcg_id}.png"
            file = discord.File(fp=image, filename=file_name)

            embed = discord.Embed(
                title=f"TCGplayer Sales — {summary.title}",
                url=f"https://www.tcgplayer.com/product/{summary.tcg_id}",
                color=color,
            )
            embed.add_field(
                name="Latest Price",
                value=f"${summary.latest_price:.2f}",
            )
            embed.add_field(
                name="Change",
                value=f"{'+' if direction >= 0 else ''}${direction:.2f}",
            )
            embed.add_field(
                name="Average Price",
                value=f"${avg_price:.2f}",
            )
            embed.add_field(name="Sales Records", value=str(summary.total_sales))
            embed.add_field(name="Total Quantity", value=str(total_quantity))
            embed.set_footer(
                text=f"Window: {window}d • Oldest price ${summary.oldest_price:.2f}"
            )

            await interaction.followup.send(embed=embed, file=file)

        @app_commands.guild_only()
        async def _alert_test_callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True)
            magic_set = _fake_magic_set()
            alert = SetAlert(
                magic_set=magic_set,
                milestone=_TEST_MILESTONE,
                scheduled_for=magic_set.observed_at,
                message="Test alert: ensure buttons and embed rendering correctly.",
            )
            await self.send_set_alerts([alert])
            await interaction.followup.send(
                "Sent test set alert to the release channel.", ephemeral=True
            )

        alert_command = app_commands.Command(
            name="alert_test",
            description="Send a test set alert (admin only)",
            callback=_alert_test_callback,
        )
        alert_command.default_member_permissions = discord.Permissions(
            manage_guild=True
        )
        alert_command.guild_only = True
        tree.add_command(alert_command, guild=self._guild)


def _build_embed(snapshot: ListingSnapshot, decision: Decision) -> discord.Embed:
    embed = discord.Embed(
        title=snapshot.title or "New listing",
        url=snapshot.url,
        description=(
            f"{snapshot.vendor.value} • {snapshot.currency} {snapshot.price:.2f}"
        ),
    )
    embed.add_field(
        name="Action",
        value=f"{decision.action.value} — {decision.rationale}",
        inline=False,
    )
    if snapshot.metadata:
        lines = [f"{k}: {v}" for k, v in snapshot.metadata.items()]
        embed.add_field(name="Details", value="\n".join(lines), inline=False)
    return embed


def _format_wishlist_entry(entry: WishlistEntry) -> str:
    parts = [f"`{entry.sku.oracle_id}` → {entry.action_preference.value}"]
    if entry.max_price is not None:
        parts.append(f"<= ${entry.max_price:.2f}")
    if entry.tags:
        parts.append(f"tags: {', '.join(entry.tags)}")
    if entry.preferred_vendors:
        vendors = ", ".join(vendor.value for vendor in entry.preferred_vendors)
        parts.append(f"vendors: {vendors}")
    return " | ".join(parts)


def _decision_link_view(decision: Decision) -> Optional[discord.ui.View]:
    snapshot = decision.event.snapshot
    if snapshot.vendor != Vendor.LOCAL_STORE:
        return None
    metadata = snapshot.metadata or {}
    contact_url = metadata.get("contact_url")
    if not contact_url:
        return None
    store_name = metadata.get("store") or "Store"
    label = store_name if len(store_name) <= 18 else f"{store_name[:15]}…"
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label=f"Contact {label}",
            style=discord.ButtonStyle.link,
            url=contact_url,
        )
    )
    return view


def _set_alert_embed(alert: SetAlert) -> discord.Embed:
    magic_set = alert.magic_set
    embed = discord.Embed(
        title=f"{magic_set.name} — {alert.milestone.value.replace('_', ' ').title()}",
        description=alert.message,
        url=magic_set.scryfall_uri,
        color=discord.Color.blurple(),
    )
    if magic_set.released_at:
        embed.add_field(name="Release Date", value=magic_set.released_at.isoformat())
    embed.add_field(name="Set Code", value=magic_set.code.upper())
    embed.add_field(name="Type", value=magic_set.set_type.title())
    if magic_set.icon_svg_uri:
        embed.set_thumbnail(url=magic_set.icon_svg_uri)
    embed.set_footer(text=f"Scheduled for {alert.scheduled_for.isoformat()}")
    return embed


def _fake_magic_set() -> MagicSet:
    today = date.today()
    return MagicSet(
        set_id="test-set",
        code="tst",
        name="Test Set Alpha",
        set_type="test",
        released_at=today,
        scryfall_uri="https://scryfall.com/sets",
        icon_svg_uri=None,
        observed_at=today,
    )


def _set_digest_embed(
    upcoming_sets: Sequence[UpcomingSet], *, title: str
) -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.gold())
    if not upcoming_sets:
        embed.description = "No sets on the horizon in the configured window."
        return embed
    lines = []
    for item in upcoming_sets:
        release = (
            item.magic_set.released_at.isoformat()
            if item.magic_set.released_at
            else "TBD"
        )
        lines.append(
            f"**{item.magic_set.name}** ({item.magic_set.code.upper()}) — releases {release} "
            f"(in {item.days_until_release} days)"
        )
    embed.description = "\n".join(lines[:10])
    if len(lines) > 10:
        embed.set_footer(text="Additional releases omitted for brevity.")
    return embed


def _magic_set_link_view(magic_set: MagicSet) -> Optional[discord.ui.View]:
    buttons: list[discord.ui.Button] = []

    if magic_set.scryfall_uri:
        buttons.append(
            discord.ui.Button(
                label="View on Scryfall",
                style=discord.ButtonStyle.link,
                url=magic_set.scryfall_uri,
            )
        )

    if magic_set.name:
        name_query = quote_plus(magic_set.name)
        tcg_url = (
            "https://www.tcgplayer.com/search/magic/product?"
            f"productLineName=magic&setName={name_query}"
        )
        buttons.append(
            discord.ui.Button(
                label="Search TCGplayer",
                style=discord.ButtonStyle.link,
                url=tcg_url,
            )
        )
        ck_url = (
            "https://www.cardkingdom.com/catalog/search"
            f"?search=header&filter%5Bname%5D={name_query}"
        )
        buttons.append(
            discord.ui.Button(
                label="Search Card Kingdom",
                style=discord.ButtonStyle.link,
                url=ck_url,
            )
        )

    if not buttons:
        return None

    view = discord.ui.View(timeout=None)
    for button in buttons[:5]:
        view.add_item(button)
    return view


def _upcoming_set_embed(item: UpcomingSet) -> discord.Embed:
    magic_set = item.magic_set
    release_text = (
        magic_set.released_at.isoformat()
        if magic_set.released_at
        else "TBD"
    )
    embed = discord.Embed(
        title=f"{magic_set.name} — releases {release_text}",
        url=magic_set.scryfall_uri,
        color=discord.Color.dark_teal(),
        description=(
            f"Releases in {item.days_until_release} day(s)."
            if item.days_until_release >= 0
            else f"Released {-item.days_until_release} day(s) ago."
        ),
    )
    embed.add_field(name="Set Code", value=magic_set.code.upper())
    embed.add_field(name="Type", value=magic_set.set_type.title())
    if magic_set.icon_svg_uri:
        embed.set_thumbnail(url=magic_set.icon_svg_uri)
    return embed


async def start_bot(client: MtgDiscordBot) -> None:
    async with client:
        await client.start(client.settings.discord.token)
