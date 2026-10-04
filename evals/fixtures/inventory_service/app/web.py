"""A deliberately small request/response layer used by the inventory service."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Handler = Callable[..., "Response"]


class HTTPError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    user: Any = None

    def json(self) -> Any:
        return json.loads(self.body or b"null")


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, payload: Any, status: int = 200) -> Response:
        body = json.dumps(payload, default=str).encode()
        return cls(status, body, {"Content-Type": "application/json"})

    @classmethod
    def file(cls, data: bytes, filename: str) -> Response:
        return cls(200, data, {"Content-Disposition": f'attachment; filename="{filename}"'})


@dataclass
class Route:
    method: str
    path: str
    handler: Handler


class Router:
    """Collects routes. `dependencies` run before every handler registered on this router."""

    def __init__(
        self, prefix: str = "", dependencies: list[Callable[[Request], None]] | None = None
    ) -> None:
        self.prefix = prefix
        self.dependencies = dependencies or []
        self.routes: list[Route] = []

    def _add(self, method: str, path: str) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            self.routes.append(Route(method, self.prefix + path, handler))
            return handler

        return decorator

    def get(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("GET", path)

    def post(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("POST", path)

    def delete(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("DELETE", path)

    def dispatch(self, route: Route, request: Request, **params: str) -> Response:
        for dependency in self.dependencies:
            dependency(request)
        return route.handler(request, **params)
