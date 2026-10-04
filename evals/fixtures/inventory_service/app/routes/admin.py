"""Administrative endpoints. Every route on this router requires the admin role."""

from __future__ import annotations

from app.auth import role_dependency
from app.db import db
from app.web import HTTPError, Request, Response, Router

router = Router(prefix="/admin", dependencies=[role_dependency("admin")])


@router.get("/users")
def list_users(request: Request) -> Response:
    rows = db.query("SELECT id, email, role, active FROM users ORDER BY id")
    return Response.json([dict(row) for row in rows])


@router.post("/users/{user_id}/deactivate")
def deactivate_user(request: Request, user_id: str) -> Response:
    changed = db.execute("UPDATE users SET active = 0 WHERE id = ?", (int(user_id),))
    if changed == 0:
        raise HTTPError(404, "user not found")
    return Response(status=204)
