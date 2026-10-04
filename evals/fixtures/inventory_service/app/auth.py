"""Authentication and authorization helpers."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from app.web import Handler, HTTPError, Request

ROLE_RANK = {"customer": 0, "staff": 1, "admin": 2}


def current_user(request: Request) -> Any:
    if request.user is None:
        raise HTTPError(401, "authentication required")
    return request.user


def require_login(handler: Handler) -> Handler:
    @functools.wraps(handler)
    def wrapper(request: Request, *args: Any, **kwargs: Any) -> Any:
        current_user(request)
        return handler(request, *args, **kwargs)

    return wrapper


def check_role(request: Request, role: str) -> None:
    user = current_user(request)
    if ROLE_RANK[user.role] < ROLE_RANK[role]:
        raise HTTPError(403, f"{role} role required")


def require_role(role: str) -> Callable[[Handler], Handler]:
    def decorator(handler: Handler) -> Handler:
        @functools.wraps(handler)
        def wrapper(request: Request, *args: Any, **kwargs: Any) -> Any:
            check_role(request, role)
            return handler(request, *args, **kwargs)

        return wrapper

    return decorator


def role_dependency(role: str) -> Callable[[Request], None]:
    """Router-level dependency: enforce `role` for every route on a router."""

    def dependency(request: Request) -> None:
        check_role(request, role)

    return dependency
