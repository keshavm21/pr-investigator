from decimal import Decimal

from app.models import OrderLine
from app.pricing import calculate_total


def test_total_includes_tax() -> None:
    lines = [OrderLine(item_id=1, quantity=2, unit_price=Decimal("10.00"))]
    assert calculate_total(lines, Decimal("0.08")) == Decimal("21.60")


def test_empty_order_is_zero() -> None:
    assert calculate_total([], Decimal("0.08")) == Decimal("0.00")
