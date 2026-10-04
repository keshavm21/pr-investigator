"""Download endpoints for generated report exports."""

from __future__ import annotations

from app.auth import require_role
from app.storage import EXPORT_DIR, safe_join
from app.web import HTTPError, Request, Response, Router

router = Router(prefix="/exports")


@router.get("/{name}")
@require_role("staff")
def download_export(request: Request, name: str) -> Response:
    try:
        path = safe_join(EXPORT_DIR, name)
    except ValueError:
        raise HTTPError(400, "invalid export name") from None
    if not path.is_file():
        raise HTTPError(404, "export not found")
    return Response.file(path.read_bytes(), filename=path.name)
