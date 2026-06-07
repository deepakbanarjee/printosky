#!/usr/bin/env python3
"""
TASK-007 — Scope Production-only Vercel env vars to the Preview environment.

Preview deployments currently fail because these secrets exist only in the
Production scope. This widens each existing variable's `target` array to also
include "preview" (= all preview branches).

This uses the Vercel REST API and only PATCHes the `target` of records that
already exist. It NEVER reads, transmits, or logs any secret value — it just
flips the scope of records Vercel already holds. The only credential needed is
a Vercel API token (read from VERCEL_TOKEN in the environment or .env).

Create a token at https://vercel.com/account/tokens (scope it to the team that
owns the printosky project), then either:

    export VERCEL_TOKEN=xxxxx           # bash
    setx VERCEL_TOKEN xxxxx             # Windows (new shells)

or add a line  VERCEL_TOKEN=xxxxx  to .env, then run:

    python scripts/scope_preview_env.py

Revoke the token afterwards if it was created just for this.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Vars that should be available to Preview deployments (currently Prod-only).
# Local-only vars (EPSON_*, STORE_*, staff PIN) are intentionally excluded.
NEED_PREVIEW = {
    "ANTHROPIC_API_KEY",
    "META_APP_SECRET",
    "META_WEBHOOK_VERIFY_TOKEN",
    "META_PHONE_NUMBER_ID",
    "META_SYSTEM_USER_TOKEN",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "SUPABASE_SERVICE_KEY",
    "PRINTOSKY_ADMIN_PASSWORD",
    "UPTIME_NOTIFY_SECRET",
}

API = "https://api.vercel.com"


def load_dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def api_call(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    dotenv = load_dotenv(root / ".env")

    token = os.environ.get("VERCEL_TOKEN") or dotenv.get("VERCEL_TOKEN")
    if not token:
        print(
            "ERROR: VERCEL_TOKEN not set (env or .env). "
            "Create one at https://vercel.com/account/tokens",
            file=sys.stderr,
        )
        return 1

    proj = json.loads((root / ".vercel" / "project.json").read_text())
    project_id = proj["projectId"]
    team_id = proj["orgId"]

    list_url = f"{API}/v9/projects/{project_id}/env?teamId={team_id}&decrypt=false"
    try:
        envs = api_call("GET", list_url, token).get("envs", [])
    except urllib.error.HTTPError as exc:
        print(f"ERROR listing env vars: {exc.code} {exc.read().decode()[:200]}", file=sys.stderr)
        return 1

    ok = err = skip = 0
    for rec in envs:
        key = rec.get("key", "")
        if key not in NEED_PREVIEW:
            continue
        target = set(rec.get("target", []))
        if "preview" in target:
            print(f"SKIP (already preview): {key}")
            skip += 1
            continue
        new_target = sorted(target | {"preview"})
        env_id = rec["id"]
        patch_url = f"{API}/v9/projects/{project_id}/env/{env_id}?teamId={team_id}"
        try:
            api_call("PATCH", patch_url, token, {"target": new_target})
            print(f"OK:   {key} -> {new_target}")
            ok += 1
        except urllib.error.HTTPError as exc:
            print(f"ERR:  {key} -> {exc.code} {exc.read().decode()[:160]}")
            err += 1

    missing = NEED_PREVIEW - {r.get("key") for r in envs}
    for key in sorted(missing):
        print(f"WARN (not found on Vercel at all): {key}")

    print(f"\nDone. {ok} widened, {skip} already-preview, {err} failed.")
    return 0 if err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
