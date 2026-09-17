"""Health and self-description.

``GET /v2/health`` reports *separate* signals — liveness, configuration,
database reachability — because F10 in the review is precisely the failure of
rolling several unrelated facts into one green dot. "Alive" does not mean
"configured", and neither means "the database answered".

``GET /v2/`` returns the live route table. A console, a store agent or a person
with curl can ask the deployment what it actually serves, instead of reading a
markdown table that was last true in April.
"""

from __future__ import annotations

import os
import time

from core import CORE_CONTRACT_VERSION
from api.v2.http import Request, Response, json_ok

_BOOTED_AT = time.time()


def index(request: Request, ctx) -> Response:
    from api.v2.app import build_router

    return json_ok(
        {
            "service": "printosky-api",
            "api_version": "2.0.0",
            "core_contract": CORE_CONTRACT_VERSION,
            "docs": "docs/V2_ARCHITECTURE.md",
            "routes": build_router().describe(),
        },
        request_id=ctx.request_id,
    )


def health(request: Request, ctx) -> Response:
    """Three independent checks. ``ok`` is the AND of the ones that must hold.

    ``database`` is only probed when ``?deep=1``: a liveness check that talks to
    Supabase on every ping turns a database blip into an outage of the health
    endpoint itself, which is how you end up unable to see what is wrong.
    """
    settings = ctx.settings
    checks = {
        "alive": {"ok": True, "detail": f"up {int(time.time() - _BOOTED_AT)}s"},
        "configured": {
            "ok": bool(settings.session_key) and bool(settings.supabase_url),
            "detail": _missing_config(settings),
        },
        "database": {"ok": None, "detail": "not probed (add ?deep=1)"},
    }

    if request.q("deep") in ("1", "true", "yes"):
        checks["database"] = _probe_database(ctx)

    hard = [c["ok"] for c in checks.values() if c["ok"] is not None]
    return json_ok(
        {
            "ok": all(hard),
            "checks": checks,
            "commit": os.environ.get("VERCEL_GIT_COMMIT_SHA", "")[:7],
            "environment": settings.environment,
            "core_contract": CORE_CONTRACT_VERSION,
        },
        status=200 if all(hard) else 503,
        request_id=ctx.request_id,
    )


def _missing_config(settings) -> str:
    missing = []
    if not settings.session_key:
        missing.append("PRINTOSKY_SESSION_KEY")
    if not settings.supabase_url:
        missing.append("SUPABASE_URL")
    if not settings.supabase_service_key:
        missing.append("SUPABASE_SERVICE_KEY")
    return ("missing: " + ", ".join(missing)) if missing else "all required settings present"


def _probe_database(ctx) -> dict:
    """One cheap read. A failure is reported as a failure, never as an empty list.

    F07 is the same mistake one layer down: ``collect_jobs`` returned ``[]`` for
    both "nothing to sync" and "could not read the table". Here the exception
    type is the answer.
    """
    started = time.time()
    try:
        ctx.uow.orders.list_orders(limit=1)
    except Exception as exc:  # noqa: BLE001 - the report is the point
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[:200]}
    return {"ok": True, "detail": f"read in {int((time.time() - started) * 1000)}ms"}
