"""Item catalogue endpoints."""

from __future__ import annotations

from decimal import Decimal

from app.auth import require_login, require_role
from app.db import db
from app.validators import parse_offset, parse_positive_int
from app.web import HTTPError, Request, Response, Router

router = Router(prefix="/items")


@router.get("")
@require_login
def list_items(request: Request) -> Response:
    limit = parse_positive_int(request.query.get("limit"), default=20, maximum=100)
    offset = parse_offset(request.query.get("offset"))
    rows = db.query(
        "SELECT id, name, price, quantity FROM items ORDER BY name LIMIT ? OFFSET ?",
        (limit, offset),
    )
    return Response.json([dict(row) for row in rows])


@router.get("/{item_id}")
@require_login
def get_item(request: Request, item_id: str) -> Response:
    row = db.query_one("SELECT id, name, price, quantity FROM items WHERE id = ?", (int(item_id),))
    if row is None:
        raise HTTPError(404, "item not found")
    return Response.json(dict(row))


@router.post("")
@require_role("staff")
def create_item(request: Request) -> Response:
    payload = request.json()
    price = Decimal(str(payload["price"]))
    if price <= 0:
        raise HTTPError(400, "price must be positive")
    db.execute(
        "INSERT INTO items (name, price, quantity) VALUES (?, ?, ?)",
        (payload["name"], str(price), int(payload.get("quantity", 0))),
    )
    return Response.json({"status": "created"}, status=201)


@router.delete("/{item_id}")
@require_role("admin")
def delete_item(request: Request, item_id: str) -> Response:
    db.execute("DELETE FROM items WHERE id = ?", (int(item_id),))
    return Response(status=204)
