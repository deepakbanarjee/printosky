"""A small, explicit router: method + path pattern → handler.

The legacy chain is ~90 sequential ``if self.path == "/x"`` comparisons split
across ``do_GET`` and ``do_POST``. It works, but it cannot express a path
parameter (hence the hand-rolled ``re.match`` calls sprinkled through it), it
answers 404 to a wrong *method* on a real path, and there is no one place to
hang authentication or logging.

This is ~100 lines and fixes all three:

    router.get("/v2/orders/{order_id}", handlers.get_order, permission="order:read")

* ``{name}`` captures one path segment (no slashes) and lands in
  ``request.params``.
* A path that exists under another method answers **405 with an Allow header**,
  not 404 — the difference between "you have a bug" and "that endpoint is gone".
* ``permission`` is declared on the route, so authorisation is data, not a line
  a new handler can forget to copy.

Route patterns are compiled once at import. Matching is a linear scan over a
few dozen compiled regexes: at Printosky's request volume that is free, and it
keeps registration order meaningful and debuggable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from core.errors import NotFoundError
from api.v2.http import Request, Response

__all__ = ["Route", "Router", "MethodNotAllowed"]

_PARAM = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class MethodNotAllowed(Exception):
    """Raised internally so the caller can attach an Allow header."""

    def __init__(self, allowed: set[str]):
        super().__init__("method not allowed")
        self.allowed = allowed


@dataclass(frozen=True)
class Route:
    method: str
    pattern: str
    handler: Callable
    permission: str | None = None
    auth: str = "required"            # required | optional | none
    name: str = ""
    regex: re.Pattern = field(default=None, repr=False)  # type: ignore[assignment]

    def describe(self) -> dict:
        return {
            "method": self.method,
            "path": self.pattern,
            "auth": self.auth,
            "permission": self.permission,
            "name": self.name or getattr(self.handler, "__name__", ""),
        }


def compile_pattern(pattern: str) -> re.Pattern:
    """``/v2/orders/{order_id}`` → a regex capturing ``order_id``.

    A parameter matches one segment and rejects an empty one, so
    ``/v2/orders//pay`` is a 404 rather than an order whose id is the empty
    string — the sort of input that reaches a database as ``eq.``.
    """
    if not pattern.startswith("/"):
        raise ValueError(f"route pattern must start with '/': {pattern!r}")
    out, last = [], 0
    for match in _PARAM.finditer(pattern):
        out.append(re.escape(pattern[last:match.start()]))
        out.append(f"(?P<{match.group(1)}>[^/]+)")
        last = match.end()
    out.append(re.escape(pattern[last:]))
    return re.compile("^" + "".join(out) + "/?$")


class Router:
    def __init__(self, prefix: str = ""):
        self.prefix = prefix.rstrip("/")
        self._routes: list[Route] = []

    # ── registration ─────────────────────────────────────────────────────────

    def add(self, method: str, pattern: str, handler: Callable, *,
            permission: str | None = None, auth: str = "required", name: str = "") -> "Router":
        full = f"{self.prefix}{pattern}" if self.prefix else pattern
        route = Route(
            method=method.upper(), pattern=full, handler=handler,
            permission=permission, auth=auth, name=name, regex=compile_pattern(full),
        )
        for existing in self._routes:
            if existing.method == route.method and existing.pattern == route.pattern:
                raise ValueError(f"duplicate route {route.method} {route.pattern}")
        self._routes.append(route)
        return self

    def get(self, pattern, handler, **kw):     return self.add("GET", pattern, handler, **kw)
    def post(self, pattern, handler, **kw):    return self.add("POST", pattern, handler, **kw)
    def patch(self, pattern, handler, **kw):   return self.add("PATCH", pattern, handler, **kw)
    def delete(self, pattern, handler, **kw):  return self.add("DELETE", pattern, handler, **kw)

    # ── matching ─────────────────────────────────────────────────────────────

    def owns(self, path: str) -> bool:
        """True if this router claims ``path`` at all.

        ``dispatch()`` uses this to decide whether to answer or to fall through
        to the legacy chain. It is prefix-based rather than route-based on
        purpose: a typo under ``/v2/`` must 404 as a v2 endpoint, not silently
        land on the legacy health check.
        """
        return bool(self.prefix) and (path == self.prefix or path.startswith(self.prefix + "/"))

    def match(self, method: str, path: str) -> tuple[Route, dict[str, str]]:
        """Return the route and its captured params, or raise.

        Raises :class:`MethodNotAllowed` when the path exists under other
        methods, :class:`core.errors.NotFoundError` when it does not exist.
        """
        method = method.upper()
        other_methods: set[str] = set()
        for route in self._routes:
            found = route.regex.match(path)
            if not found:
                continue
            if route.method == method:
                return route, found.groupdict()
            other_methods.add(route.method)
        if other_methods:
            raise MethodNotAllowed(other_methods | {"OPTIONS"})
        raise NotFoundError(f"no v2 route for {method} {path}", details={"path": path})

    def routes(self) -> list[Route]:
        return list(self._routes)

    def describe(self) -> list[dict]:
        """Machine-readable route table — served at ``GET /v2/`` as live docs."""
        return [r.describe() for r in sorted(self._routes, key=lambda r: (r.pattern, r.method))]


def bind(route: Route, request: Request, params: dict[str, str], ctx) -> Response:
    """Call a route handler with the arguments it declares.

    Handlers are plain functions taking ``(request, ctx)``; params arrive on
    ``request.params``. Keeping the signature uniform means middleware can wrap
    any handler without knowing anything about it.
    """
    request.params = params
    return route.handler(request, ctx)
