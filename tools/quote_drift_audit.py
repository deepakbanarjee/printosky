#!/usr/bin/env python3
"""Quote-drift audit — does every stored quote still match what the rate card says?

Recomputes every job's `print_spec` through `rate_card.calculate_quote`, using the
same item-build logic as `api/handlers_order.py:_handle_order_create`, and reports
where the recomputed total disagrees with the `amount_quoted` actually charged.

Two things it is looking for:

  1. **Drift** — a stored quote the current code would not produce. Expected for
     jobs priced before a deliberate rate change (e.g. commit 6afb9b5, which
     removed the odd-sheet rounding on 2026-08-14); a *recent* drift row means a
     quote path and the rate card have come apart, and that is a bug.
  2. **Unrated paper types** — a size the order page offers and `_VALID_SIZE`
     accepts, but `PRINT_RATES` has no key for. `get_print_rate` falls back to
     `A4_BW` (Rs.3/sheet) for those, silently, colour included.

Read-only. Run:  SUPABASE_URL=... SUPABASE_SERVICE_KEY=... python tools/quote_drift_audit.py
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rate_card

# Mirrors api/handlers_order.py — keep in step with it, or the audit measures
# the wrong thing.
VALID_FINISHING = {"none", "staple", "spiral", "wiro",
                   "soft", "perfect", "project", "record", "thesis"}
LAYOUT = {1: "1-up", 2: "2-up", 4: "4-up", 6: "4-up", 9: "4-up"}
SELECT = ("job_id,received_at,amount_quoted,print_spec"
          "&print_spec=not.is.null&order=received_at")


def fetch_jobs() -> list[dict]:
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
    req = urllib.request.Request(
        f"{url}/rest/v1/jobs?select={SELECT}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def recompute(spec: dict) -> float:
    """The price api/handlers_order.py would charge for this spec today."""
    size = spec.get("paper_size") or "A4"
    colour = spec.get("colour_mode") or "bw"
    inc = len(spec.get("pages_included") or [])
    col_n = (len(spec.get("colour_pages") or []) if colour == "mixed"
             else (inc if colour == "col" else 0))
    bw_n = inc - col_n
    sides = "ds" if spec.get("sides") == "duplex" else "ss"
    layout = LAYOUT.get(int(spec.get("nup") or 1), "1-up")
    copies = int(spec.get("copies") or 1)

    items = []
    if bw_n > 0 or col_n == 0:
        items.append({"pages": bw_n, "paper_type": f"{size}_BW",
                      "sides": sides, "layout": layout, "copies": copies})
    if col_n > 0:
        items.append({"pages": col_n, "paper_type": f"{size}_col",
                      "sides": sides, "layout": layout, "copies": copies})
    finishing = spec.get("binding") if spec.get("binding") in VALID_FINISHING else "none"
    return rate_card.calculate_quote(items, finishing=finishing, paper_size=size)["total"]


def unrated_types(spec: dict) -> list[str]:
    """Paper types this spec needs that PRINT_RATES has no entry for.

    A4_col is resolved to a tier inside get_print_rate rather than being a key,
    so it is rated even though the literal key is absent.
    """
    size = spec.get("paper_size") or "A4"
    colour = spec.get("colour_mode") or "bw"
    wanted = {f"{size}_BW"}
    if colour in ("col", "mixed"):
        wanted.add(f"{size}_col")
    return sorted(t for t in wanted
                  if t not in rate_card.PRINT_RATES and t != "A4_col")


def main() -> int:
    jobs = fetch_jobs()
    drift, unrated, skipped, matched = [], [], [], 0

    for job in jobs:
        spec = job.get("print_spec") or {}
        stored = job.get("amount_quoted")
        if stored is None:
            continue
        for t in unrated_types(spec):
            unrated.append((job, t))
        if not (spec.get("pages_included") or []):
            skipped.append(job)          # pre-dates the field; nothing to recompute
            continue
        got = recompute(spec)
        if abs(got - float(stored)) > 0.01:
            drift.append((job, float(stored), got))
        else:
            matched += 1

    print(f"jobs with a stored quote : {matched + len(drift) + len(skipped)}")
    print(f"  match the rate card    : {matched}")
    print(f"  DRIFT                  : {len(drift)}")
    print(f"  not recomputable       : {len(skipped)} (spec has no pages_included)")
    print(f"  unrated paper types    : {len(unrated)}")

    if drift:
        print("\n=== DRIFT — stored quote is not what the rate card produces now ===")
        for job, stored, got in drift:
            spec = job["print_spec"]
            print(f"  {job['job_id']}  {job['received_at'][:10]}  "
                  f"{spec.get('paper_size')}/{spec.get('colour_mode')}  "
                  f"nup={spec.get('nup')} {spec.get('sides')}  "
                  f"stored Rs.{stored:.0f} -> now Rs.{got:.0f}  ({got - stored:+.0f})")
        newest = max(j["received_at"][:10] for j, _, _ in drift)
        print(f"\n  newest drift row: {newest} — drift only before a deliberate rate "
              f"change is expected; drift after one is a bug.")

    if unrated:
        print("\n=== UNRATED PAPER TYPES — silently billed at A4 B&W (Rs.3/sheet) ===")
        for job, t in unrated:
            print(f"  {job['job_id']}  {job['received_at'][:10]}  needs {t}")

    print("\n=== PRINT_RATES coverage vs the sizes the order page offers ===")
    for size in ("A4", "A3", "A5", "Legal", "Letter"):
        for suffix in ("BW", "col"):
            key = f"{size}_{suffix}"
            ok = key in rate_card.PRINT_RATES or key == "A4_col"
            print(f"  {key:12} {'ok' if ok else 'MISSING -> falls back to A4_BW'}")

    return 1 if (drift or unrated) else 0


if __name__ == "__main__":
    raise SystemExit(main())
