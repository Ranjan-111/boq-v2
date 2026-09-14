"""Router infrastructure shared by every write surface.

Standalone module with no repo imports so it cannot create cycles: auth is
imported BY scope (auth's require_user), so the write-committing router class
cannot live in scope.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.routing import APIRoute

_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class CommitOnWriteRoute(APIRoute):
    """Route whose mutating handlers commit BEFORE the response is sent.

    session_dependency's teardown commit runs after the response bytes leave
    (FastAPI >=0.106 teardown ordering), so a 2xx could reach the client
    before its transaction is durable — and the client's very next request
    (TanStack refetch, an E2E seed, any chained call) may SELECT first and
    see pre-commit state. CI e2e hit exactly this on a loaded runner:
    201 register followed 3ms later by 401 unknown_user, and 201
    catalog/items followed by 404 on the rate PUT for that very item.

    This wrapper runs AFTER the user handler returns (its dict is already
    serialized into a Response) but BEFORE starlette sends it — the commit
    lands first, so 2xx-implies-durable holds for every mutating route that
    uses this class. Errors raise before the wrapper body, leaving the
    teardown rollback in charge; a commit failure here replaces the pending
    2xx with a 500 and rolls back (no partial write is ever acknowledged).
    """

    def get_route_handler(self) -> Callable[[Request], Any]:
        original = super().get_route_handler()

        async def commit_before_send(request: Request) -> Any:
            response = await original(request)
            if request.method in _MUTATING:
                session = getattr(request.state, "db_session", None)
                if session is not None:
                    await session.commit()
            return response

        return commit_before_send


class CommitOnWriteRouter(APIRouter):
    """APIRouter wired to CommitOnWriteRoute (backend write surfaces)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("route_class", CommitOnWriteRoute)
        super().__init__(*args, **kwargs)
