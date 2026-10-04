"""Order pricing."""

from __future__ import annotations

from decimal import Decimal

from app.models import OrderLine

CENT = Decimal("0.01")


def calculate_total(lines: list[OrderLine], tax_rate: Decimal) -> Decimal:
    """Return the order total including tax, rounded to cents."""
    subtotal = sum((line.unit_price * line.quantity for line in lines), Decimal("0"))
    return (subtotal * (1 + tax_rate)).quantize(CENT)
