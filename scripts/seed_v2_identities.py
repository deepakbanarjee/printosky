#!/usr/bin/env python
"""Seed ``identities`` from the existing ``staff`` table.

The v2 API authenticates against ``identities`` (one row per person or device,
with a role and a store scope). Everybody who can already sign in today lives in
``staff``, with a PBKDF2 hash and salt that :func:`core.identity.verify_secret`
understands unchanged — so nobody has to be issued a new PIN to move over.

    python scripts/seed_v2_identities.py --dry-run      # show the plan
    python scripts/seed_v2_identities.py                # write it
    python scripts/seed_v2_identities.py --agent OSP    # add a store-agent row

Roles are not guessable from the ``staff`` table, so every migrated row lands as
``counter`` — the least privileged role that can still do a counter's job — and
the owner promotes the right people afterwards:

    update identities set role = 'owner'   where identity_id = 'deepak';
    update identities set role = 'manager' where identity_id = 'anu';

Idempotent: re-running updates the hash/scope of rows it already created and
never downgrades a role that was changed by hand.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.identity import Role  # noqa: E402

DEFAULT_ROLE = Role.COUNTER.value
DEFAULT_STORES = ("OSP",)


def _client():
    from db_cloud import _client as db_client

    return db_client()


def plan_from_staff(rows: list[dict], stores: tuple[str, ...]) -> list[dict]:
    """Turn ``staff`` rows into ``identities`` rows. Pure, so it is testable."""
    out = []
    for row in rows:
        staff_id = str(row.get("id") or "").strip().lower()
        if not staff_id:
            continue
        if not row.get("pin_hash"):
            # No credential means no login. Skipping loudly beats creating an
            # identity nobody can use and everybody assumes works.
            print(f"  ! skipping {staff_id}: no pin_hash", file=sys.stderr)
            continue
        out.append(
            {
                "identity_id": staff_id,
                "display_name": row.get("name") or staff_id,
                "role": DEFAULT_ROLE,
                "store_ids": list(row.get("store_ids") or stores),
                "kind": "staff",
                "secret_hash": row["pin_hash"],
                "secret_salt": row.get("pin_salt"),
                "active": bool(row.get("active", 1)),
                "metadata": {"migrated_from": "staff"},
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    parser.add_argument("--stores", default=",".join(DEFAULT_STORES),
                        help="comma-separated default store scope for migrated staff")
    parser.add_argument("--agent", metavar="STORE_ID",
                        help="also create a device identity for this store's PC")
    args = parser.parse_args()

    if not os.environ.get("SUPABASE_URL"):
        print("SUPABASE_URL is not set — refusing to guess a database", file=sys.stderr)
        return 2

    client = _client()
    staff = client.table("staff").select("*").execute().data or []
    rows = plan_from_staff(staff, tuple(s for s in args.stores.split(",") if s))

    if args.agent:
        rows.append(
            {
                "identity_id": f"agent:{args.agent}",
                "display_name": f"{args.agent} store PC",
                "role": Role.AGENT.value,
                "store_ids": [args.agent],
                "kind": "device",
                # Agents authenticate with PRINTOSKY_AGENT_TOKEN, not with this
                # column; the unusable placeholder is deliberate, so that a
                # secret-based login can never match this row.
                "secret_hash": "agent-token-auth",
                "secret_salt": None,
                "active": True,
                "metadata": {"auth": "PRINTOSKY_AGENT_TOKEN"},
            }
        )

    print(f"{len(rows)} identities to upsert:")
    for row in rows:
        print(f"  {row['identity_id']:<16} {row['role']:<10} {row['store_ids']}")
    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    existing = {
        r["identity_id"]: r
        for r in (client.table("identities").select("identity_id,role").execute().data or [])
    }
    for row in rows:
        prior = existing.get(row["identity_id"])
        if prior and prior.get("role") not in (None, DEFAULT_ROLE):
            row["role"] = prior["role"]     # never undo a hand-set role
    client.table("identities").upsert(rows, on_conflict="identity_id").execute()
    print(f"\nwrote {len(rows)} identities")
    print("Now set the real roles:  update identities set role='owner' where identity_id='…';")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
