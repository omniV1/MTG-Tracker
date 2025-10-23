"""Service layer orchestrating wishlist persistence and decision engine updates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from mtgbot.models import ActionType, CardSku, RoleMapping, Vendor, WishlistEntry
from mtgbot.storage.wishlist import RoleMappingRepository, WishlistRepository


def _parse_tags(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [tag.strip().lower() for tag in raw.split(",") if tag.strip()]


def _parse_vendors(raw: Optional[str]) -> List[Vendor]:
    if not raw:
        return []
    values = [part.strip().lower() for part in raw.split(",") if part.strip()]
    vendors: List[Vendor] = []
    for value in values:
        try:
            vendors.append(Vendor(value))
        except ValueError:
            continue
    return vendors


class DecisionEngineProtocol:
    """Lightweight protocol so the service can work with the decision engine."""

    def register(self, wishlist: WishlistEntry) -> None:  # pragma: no cover - protocol
        raise NotImplementedError

    def unregister(self, discord_user_id: int, oracle_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def reset(self, entries: Sequence[WishlistEntry]) -> None:  # pragma: no cover
        raise NotImplementedError


@dataclass
class WishlistService:
    repository: WishlistRepository
    role_repository: RoleMappingRepository
    engine: DecisionEngineProtocol
    _role_cache: dict[str, int] = None

    async def initialize(self) -> None:
        await self.repository.init()
        await self.role_repository.init()
        entries = await self.repository.list_all_entries()
        self.engine.reset(entries)
        mappings = await self.role_repository.list_mappings()
        self._role_cache = {mapping.tag.lower(): mapping.role_id for mapping in mappings}

    async def add_or_update_entry(
        self,
        discord_user_id: int,
        identifier: str,
        *,
        max_price: Optional[float],
        action: ActionType,
        tags: Optional[str],
        preferred_vendors: Optional[str],
    ) -> WishlistEntry:
        sku = CardSku(
            oracle_id=identifier,
            product_code=identifier,
            finish="any",
        )
        tag_list = _parse_tags(tags)
        vendor_list = _parse_vendors(preferred_vendors)
        entry = WishlistEntry(
            discord_user_id=discord_user_id,
            sku=sku,
            max_price=max_price,
            action_preference=action,
            tags=tag_list,
            preferred_vendors=vendor_list,
        )
        await self.repository.upsert_entry(entry)
        self.engine.register(entry)
        return entry

    async def remove_entry(self, discord_user_id: int, identifier: str) -> bool:
        removed = await self.repository.remove_entry(discord_user_id, identifier)
        if removed:
            self.engine.unregister(discord_user_id, identifier)
        return removed

    async def list_entries(self, discord_user_id: int) -> Sequence[WishlistEntry]:
        return await self.repository.list_entries_for_user(discord_user_id)

    async def list_all_entries(self) -> Sequence[WishlistEntry]:
        return await self.repository.list_all_entries()

    async def set_role_mapping(self, tag: str, role_id: int) -> RoleMapping:
        mapping = RoleMapping(tag=tag.lower(), role_id=role_id)
        await self.role_repository.upsert_mapping(mapping)
        if self._role_cache is None:
            self._role_cache = {}
        self._role_cache[mapping.tag] = role_id
        return mapping

    async def remove_role_mapping(self, tag: str) -> bool:
        removed = await self.role_repository.remove_mapping(tag.lower())
        if removed and self._role_cache and tag.lower() in self._role_cache:
            del self._role_cache[tag.lower()]
        return removed

    async def list_role_mappings(self) -> Sequence[RoleMapping]:
        mappings = await self.role_repository.list_mappings()
        self._role_cache = {mapping.tag.lower(): mapping.role_id for mapping in mappings}
        return mappings

    def resolve_role_mentions(self, tags: Iterable[str]) -> Optional[str]:
        if not tags:
            return None
        if self._role_cache is None:
            return None
        role_ids = [
            self._role_cache[tag.lower()]
            for tag in tags
            if tag.lower() in self._role_cache
        ]
        if not role_ids:
            return None
        return " ".join(f"<@&{role_id}>" for role_id in role_ids)
