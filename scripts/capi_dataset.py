# -*- coding: utf-8 -*-
"""Find (or create) the Conversions API dataset, without hunting through the UI.

Events Manager lists Meta *apps* and web datasets in the same place and calls
both "data sources", so "find the Dataset ID" turns into a scavenger hunt: the
panel shows an App ID where you expect a Dataset ID, and the Datasets list can
look full while containing no dataset at all.

  python scripts/capi_dataset.py            # find what already exists
  python scripts/capi_dataset.py --create   # create one, only if none exists

The id it prints is META_CAPI_DATASET_ID (see meta_capi.py). Reads the token
from .env: META_ACCESS_TOKEN, INSTAGRAM_PAGE_ACCESS_TOKEN, META_SYSTEM_USER_TOKEN
or META_CAPI_TOKEN -- whichever is set. Needs ads_management / business_management.

LOOKS IN TWO PLACES, which is the whole point
---------------------------------------------
A dataset can hang off the AD ACCOUNT or off the BUSINESS PORTFOLIO, and they
are different edges on the Graph. Checking only the business is how you end up
concluding none exists and trying to create one -- at which point Meta says
"you can create only 1 dataset per account", because there already was one on
the ad account all along.
"""
import argparse
import os
import sys

import requests

GRAPH = "https://graph.facebook.com/v21.0"
BUSINESS_ID = os.environ.get("META_BUSINESS_ID", "946245207850844")      # Printosky.com
AD_ACCOUNT_ID = os.environ.get("META_AD_ACCOUNT_ID", "629683978054076")
DEFAULT_NAME = "Printosky CAPI"

_TOKEN_VARS = ("META_ACCESS_TOKEN", "INSTAGRAM_PAGE_ACCESS_TOKEN",
               "META_SYSTEM_USER_TOKEN", "META_CAPI_TOKEN")


def _token():
    for var in _TOKEN_VARS:
        val = (os.environ.get(var) or "").strip()
        if val:
            return val, var
    try:
        with open(".env", encoding="utf-8") as fh:
            body = fh.read()
    except OSError:
        return "", ""
    for var in _TOKEN_VARS:
        for line in body.splitlines():
            if line.strip().startswith(f"{var}="):
                return line.split("=", 1)[1].strip().strip("'\""), var
    return "", ""


def _act(account_id: str) -> str:
    account_id = (account_id or "").strip()
    return account_id if account_id.startswith("act_") else f"act_{account_id}"


def _fetch(path: str, token: str):
    """Returns (rows, error). An error on one edge must not hide the other."""
    try:
        r = requests.get(f"{GRAPH}/{path}",
                         params={"fields": "id,name,last_fired_time",
                                 "access_token": token}, timeout=30)
        data = r.json()
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"
    if "error" in data:
        return [], data["error"].get("message", "unknown error")
    return (data.get("data") or []), ""


def find_datasets(token: str):
    """Every dataset reachable from either edge, de-duplicated by id."""
    found, errors = {}, []
    for label, path in (("ad account", f"{_act(AD_ACCOUNT_ID)}/adspixels"),
                        ("business", f"{BUSINESS_ID}/owned_pixels")):
        rows, err = _fetch(path, token)
        if err:
            errors.append(f"  {label}: {err}")
        for row in rows:
            found.setdefault(row["id"], {**row, "where": label})
    return list(found.values()), errors


def main():
    ap = argparse.ArgumentParser(description="Find or create the CAPI dataset")
    ap.add_argument("--create", action="store_true",
                    help="create one (refuses if a dataset already exists)")
    ap.add_argument("--name", default=DEFAULT_NAME, help=f"name (default: {DEFAULT_NAME})")
    args = ap.parse_args()

    token, src = _token()
    if not token:
        print(f"No token found. Set one of: {', '.join(_TOKEN_VARS)}")
        sys.exit(1)
    print(f"Ad account {_act(AD_ACCOUNT_ID)}, business {BUSINESS_ID}, "
          f"token from {src}\n")

    rows, errors = find_datasets(token)
    for err in errors:
        print(f"[warn] could not read one source:\n{err}")

    if rows:
        print(f"{len(rows)} dataset(s) -- any of these can be META_CAPI_DATASET_ID:\n")
        for p in rows:
            fired = p.get("last_fired_time") or "never received an event"
            print(f"  {p['id']}  {p.get('name', '?')}  (on the {p['where']})")
            print(f"      last event: {fired}")
        if args.create:
            # Meta allows one dataset per ad account, so a second attempt is
            # refused anyway -- say so here rather than relaying that error.
            print("\nNot creating: a dataset already exists (Meta allows one "
                  "per ad account). Use the id above.")
        return

    if not args.create:
        print("No dataset found on either the ad account or the business.\n"
              "  python scripts/capi_dataset.py --create")
        return

    r = requests.post(f"{GRAPH}/{BUSINESS_ID}/adspixels",
                      data={"name": args.name, "access_token": token}, timeout=30)
    data = r.json()
    if "error" in data:
        print(f"Create failed: {data['error'].get('message')}")
        sys.exit(1)
    print(f"Created '{args.name}'\n\n  META_CAPI_DATASET_ID={data['id']}\n")


if __name__ == "__main__":
    main()
