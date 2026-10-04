"""Application entry point: registers every router."""

from __future__ import annotations

from app.routes import admin, exports, items, orders

ROUTERS = [items.router, orders.router, admin.router, exports.router]
