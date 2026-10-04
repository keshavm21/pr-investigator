"""Plain data objects."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class User:
    id: int
    email: str
    role: str
    active: bool = True


@dataclass
class Item:
    id: int
    name: str
    price: Decimal
    quantity: int
    owner_id: int | None = None


@dataclass
class OrderLine:
    item_id: int
    quantity: int
    unit_price: Decimal
