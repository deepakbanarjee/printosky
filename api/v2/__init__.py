"""Printosky API v2 — the transport layer for :mod:`core`.

Mounted *in front of* the existing if-chain in ``api/index.py`` and matching
only paths under ``/v2/``. Every legacy route keeps its exact current
behaviour: ``dispatch()`` returns False for anything it does not own, and the
old chain runs as before. That is the whole migration strategy — a strangler
fig, not a rewrite.

Layout:
    http.py     Request/Response, the JSON envelope, CORS, request ids
    router.py   method + path-pattern matching with typed params
    deps.py     per-request context: settings, unit of work, principal
    repos.py    in-memory adapters (tests, and the store agent's future base)
    repos_supabase.py  the cloud adapters
    routes_*.py the endpoints themselves
    app.py      wiring + the dispatch() entry point api/index.py calls
"""

from api.v2.app import dispatch, build_router  # noqa: F401  (public entry points)

__all__ = ["dispatch", "build_router"]

API_VERSION = "2.0.0"
