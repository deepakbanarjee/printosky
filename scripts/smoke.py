#!/usr/bin/env python3
"""
TASK-017 — End-to-end smoke test (single command).

Proves the deployed Printosky API is up, correctly routed, env-complete, and
properly secured — the exact failure modes that have bitten us (routes 404ing,
auth misconfigured, missing env vars, a signature check crashing with 500).

Design choice: this is a SAFE probe. It does NOT send WhatsApp messages, create
jobs, or touch Razorpay money — so it's safe to run every few hours against
production and from CI/cron. It exercises the real endpoints using health +
NEGATIVE-auth checks (wrong token / no auth / bad signature), which require no
secrets. If secrets are present in the env, a few POSITIVE checks also run.

(The full happy-path flow — fake media webhook -> job -> bot replies -> payment
-> Paid -> pickup notify — is intentionally out of scope here: it has real side
effects, costs WhatsApp messages on every run, and exercises the Razorpay path
which is not yet active in production. Add it as an opt-in non-prod test later.)

Usage:
    python scripts/smoke.py                          # against production
    python scripts/smoke.py --url http://localhost:3005
    python scripts/smoke.py --json                   # machine-readable output

Optional positive checks run automatically when these env vars are set:
    META_WEBHOOK_VERIFY_TOKEN   -> Meta webhook verify echoes the challenge
    PRINTOSKY_ADMIN_PASSWORD    -> admin endpoint returns 200 with right pass

Exit codes:
    0  all required checks passed
    1  one or more required checks failed
    2  usage / unreachable host
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "https://printosky.vercel.app"
TIMEOUT = 15


class Result:
    def __init__(self, name: str, ok: bool, detail: str, required: bool = True):
        self.name = name
        self.ok = ok
        self.detail = detail
        self.required = required


def _request(method: str, url: str, headers: dict | None = None,
             body: bytes | None = None) -> tuple[int, str]:
    """Return (status_code, body_text). Network errors -> (0, reason)."""
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(4096).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 — surface any network failure as 0
        return 0, f"{type(e).__name__}: {e}"


def run_checks(base: str, env: dict) -> list[Result]:
    base = base.rstrip("/")
    results: list[Result] = []

    # 1. Health endpoint up + all subsystems present.
    code, body = _request("GET", f"{base}/api/health")
    ok = code == 200
    detail = f"{code}"
    if ok:
        try:
            payload = json.loads(body)
            checks = payload.get("checks", {})
            missing = [k for k, v in checks.items() if not v]
            ok = bool(payload.get("ok")) and not missing
            detail = f"{code} ok={payload.get('ok')}" + (f" MISSING={missing}" if missing else "")
        except Exception:
            ok = False
            detail = f"{code} (unparseable body)"
    results.append(Result("health 200 + env complete", ok, detail))

    # 2. Meta webhook verify rejects a wrong verify token.
    q = urllib.parse.urlencode({"hub.mode": "subscribe",
                                "hub.verify_token": "smoke-wrong-token",
                                "hub.challenge": "smoke123"})
    code, _ = _request("GET", f"{base}/whatsapp-webhook?{q}")
    results.append(Result("meta verify rejects bad token", code == 403, f"{code} (want 403)"))

    # 3. Razorpay webhook runs signature verification (bad sig -> 400, not 200/500).
    code, _ = _request("POST", f"{base}/webhook/razorpay",
                       headers={"X-Razorpay-Signature": "smoke-bad", "Content-Type": "application/json"},
                       body=b'{"event":"smoke"}')
    results.append(Result("razorpay rejects bad signature", code == 400, f"{code} (want 400)"))

    # 4. Admin endpoint is auth-gated.
    code, _ = _request("GET", f"{base}/admin/conversations")
    results.append(Result("admin endpoint gated", code in (401, 403), f"{code} (want 401/403)"))

    # 5. Cron endpoint is auth-gated.
    code, _ = _request("GET", f"{base}/cron/daily-activity",
                       headers={"Authorization": "Bearer smoke-wrong"})
    results.append(Result("cron endpoint gated", code == 401, f"{code} (want 401)"))

    # 6. Root responds.
    code, _ = _request("GET", f"{base}/")
    results.append(Result("root responds", code == 200, f"{code} (want 200)"))

    # ── Optional positive checks (only when secrets are present) ──────────────
    verify_token = env.get("META_WEBHOOK_VERIFY_TOKEN")
    if verify_token:
        nonce = "smoke-ok-9931"
        q = urllib.parse.urlencode({"hub.mode": "subscribe",
                                    "hub.verify_token": verify_token,
                                    "hub.challenge": nonce})
        code, body = _request("GET", f"{base}/whatsapp-webhook?{q}")
        ok = code == 200 and body.strip() == nonce
        results.append(Result("meta verify echoes challenge", ok,
                              f"{code} body={body.strip()[:20]!r}", required=True))

    admin_pw = env.get("PRINTOSKY_ADMIN_PASSWORD")
    if admin_pw:
        code, _ = _request("GET", f"{base}/admin/conversations",
                           headers={"X-Admin-Password": admin_pw})
        results.append(Result("admin auth accepts right password", code == 200,
                              f"{code} (want 200)", required=True))

    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Printosky deployment smoke test.")
    ap.add_argument("--url", default=os.environ.get("PRINTOSKY_SMOKE_URL", DEFAULT_URL),
                    help=f"Base URL to probe (default {DEFAULT_URL})")
    ap.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    ap.add_argument("--dotenv", metavar="PATH",
                    help="Load this .env first (enables the optional positive checks)")
    args = ap.parse_args()

    env = dict(os.environ)
    if args.dotenv and os.path.exists(args.dotenv):
        for line in open(args.dotenv, encoding="utf-8").read().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())

    results = run_checks(args.url, env)
    failed = [r for r in results if r.required and not r.ok]

    if args.json:
        print(json.dumps({
            "url": args.url,
            "passed": len(results) - len(failed),
            "failed": len(failed),
            "checks": [{"name": r.name, "ok": r.ok, "detail": r.detail} for r in results],
        }, indent=2))
    else:
        print(f"Smoke test against {args.url}\n")
        for r in results:
            mark = "PASS" if r.ok else "FAIL"
            print(f"  [{mark}] {r.name:38s} {r.detail}")
        print()
        if failed:
            print(f"RESULT: FAIL - {len(failed)}/{len(results)} checks failed")
        else:
            print(f"RESULT: PASS - all {len(results)} checks passed")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
