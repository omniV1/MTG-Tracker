"""Core domain models used across the MTG bot services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Dict, List, Optional


class ProductKind(str, Enum):
    SINGLE = "single"
    SEALED = "sealed"
    DIGITAL = "digital"


class Vendor(str, Enum):
    CARD_KINGDOM = "card_kingdom"
    COOL_STUFF_INC = "cool_stuff_inc"
    MINIATURE_MARKET = "miniature_market"
    STAR_CITY_GAMES = "star_city_games"
    TCGPLAYER = "tcgplayer"
    CARDHOARDER = "cardhoarder"
    GOATBOTS = "goatbots"
    AMAZON = "amazon"
    TARGET = "target"
    BEST_BUY = "best_buy"
    WALMART = "walmart"
    WIZARDS_STORE = "wizards_store"
    LOCAL_STORE = "local_store"


@dataclass(slots=True)
class CardSku:
    """Represents a product variant for matching retailer listings."""

    oracle_id: str
    product_code: str
    finish: str
    collector_number: Optional[str] = None
    set_code: Optional[str] = None
    vendor_sku: Optional[str] = None


@dataclass(slots=True)
class ListingSnapshot:
    """Normalized view of a retailer listing."""

    vendor: Vendor
    sku: CardSku
    title: str
    url: str
    price: float
    currency: str
    available: bool
    observed_at: datetime
    metadata: Dict[str, str] = field(default_factory=dict)


class ActionType(str, Enum):
    NOTIFY = "notify"
    CART = "cart"


@dataclass(slots=True)
class InventoryEvent:
    """Triggered when a listing flips availability or price crosses a threshold."""

    snapshot: ListingSnapshot
    previous_snapshot: Optional[ListingSnapshot]
    event_type: str  # e.g. restock, price_drop
    delta_price: Optional[float] = None


@dataclass(slots=True)
class WishlistEntry:
    discord_user_id: int
    sku: CardSku
    max_price: Optional[float]
    action_preference: ActionType
    tags: List[str] = field(default_factory=list)
    preferred_vendors: List[Vendor] = field(default_factory=list)


@dataclass(slots=True)
class Decision:
    event: InventoryEvent
    wishlist: WishlistEntry
    action: ActionType
    rationale: str


@dataclass(slots=True)
class RoleMapping:
    tag: str
    role_id: int


class SetMilestone(str, Enum):
    ANNOUNCEMENT = "announcement"
    T_MINUS_30 = "t_minus_30"
    T_MINUS_14 = "t_minus_14"
    T_MINUS_7 = "t_minus_7"
    T_MINUS_1 = "t_minus_1"
    RELEASE_DAY = "release_day"


@dataclass(slots=True)
class MagicSet:
    set_id: str
    code: str
    name: str
    set_type: str
    released_at: Optional[date]
    scryfall_uri: str
    icon_svg_uri: Optional[str]
    observed_at: date


@dataclass(slots=True)
class SetAlert:
    magic_set: MagicSet
    milestone: SetMilestone
    scheduled_for: date
    message: str
