"""Order endpoints."""

from __future__ import annotations

from decimal import Decimal

from app.auth import current_user, require_login
from app.db import db
from app.models import OrderLine
from app.pricing import calculate_total
from app.web import HTTPError, Request, Response, Router

router = Router(prefix="/orders")
TAX_RATE = Decimal("0.08")


@router.post("")
@require_login
def create_order(request: Request) -> Response:
    user = current_user(request)
    payload = request.json()
    lines: list[OrderLine] = []
    for entry in payload["lines"]:
        item_id = int(entry["item_id"])
        row = db.query_one("SELECT price, quantity FROM items WHERE id = ?", (item_id,))
        if row is None:
            raise HTTPError(404, f"item {item_id} not found")
        quantity = int(entry["quantity"])
        if quantity < 1 or quantity > row["quantity"]:
            raise HTTPError(400, "invalid quantity")
        lines.append(OrderLine(item_id, quantity, Decimal(row["price"])))
    total = calculate_total(lines, TAX_RATE)
    db.execute("INSERT INTO orders (user_id, total) VALUES (?, ?)", (user.id, str(total)))
    for line in lines:
        db.execute(
            "UPDATE items SET quantity = quantity - ? WHERE id = ?", (line.quantity, line.item_id)
        )
    return Response.json({"total": str(total)}, status=201)
