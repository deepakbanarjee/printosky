# -*- coding: utf-8 -*-
"""List or create the Conversions API dataset, without hunting through the UI.

Events Manager lists Meta *apps* and web datasets in the same place and calls
both "data sources", which makes "find the Dataset ID" a scavenger hunt. This
asks the Graph API instead.

  python scripts/capi_dataset.py            # list existing datasets
  python scripts/capi_dataset.py --create   # create one named Printosky CAPI

The id it prints is META_CAPI_DATASET_ID (see meta_capi.py). Reads the token
from .env: META_ACCESS_TOKEN, or INSTAGRAM_PAGE_ACCESS_TOKEN, or
META_SYSTEM_USER_TOKEN -- whichever is set. Needs business_management.
"""
import argparse
import os
import sys

import requests

GRAPH = "https://graph.facebook.com/v21.0"
BUSINESS_ID = os.environ.get("META_BUSINESS_ID", "946245207850844")  # Printosky.com
DEFAULT_NAME = "Printosky CAPI"

_TOKEN_VARS = ("META_ACCESS_TOKEN", "INSTAGRAM_PAGE_ACCESS_TOKEN",
               "META_SYSTEM_USER_TOKEN", "META_CAPI_TOKEN")


def _token():
    for var in _TOKEN_VARS:
        val = (os.environ.get(var) or "").strip()
        if val:
            return val, var
    # Fall back to .env, which is where this project keeps them.
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


def main():
    ap = argparse.ArgumentParser(description="List or create the CAPI dataset")
    ap.add_argument("--create", action="store_true", help="create a dataset")
    ap.add_argument("--name", default=DEFAULT_NAME, help=f"name (default: {DEFAULT_NAME})")
    args = ap.parse_args()

    token, src = _token()
    if not token:
        print(f"No token found. Set one of: {', '.join(_TOKEN_VARS)}")
        sys.exit(1)
    print(f"Business {BUSINESS_ID}, token from {src}\n")

    if args.create:
        r = requests.post(f"{GRAPH}/{BUSINESS_ID}/adspixels",
                          data={"name": args.name, "access_token": token}, timeout=30)
        data = r.json()
        if "error" in data:
            print(f"Create failed: {data['error'].get('message')}")
            sys.exit(1)
        print(f"Created '{args.name}'\n\n  META_CAPI_DATASET_ID={data['id']}\n")
        return

    r = requests.get(f"{GRAPH}/{BUSINESS_ID}/owned_pixels",
                     params={"fields": "id,name,last_fired_time",
                             "access_token": token}, timeout=30)
    data = r.json()
    if "error" in data:
        print(f"List failed: {data['error'].get('message')}")
        sys.exit(1)
    rows = data.get("data") or []
    if not rows:
        print("No datasets on this business yet. Create one:\n"
              "  python scripts/capi_dataset.py --create")
        return
    print(f"{len(rows)} dataset(s) -- any of these can be META_CAPI_DATASET_ID:\n")
    for p in rows:
        fired = p.get("last_fired_time") or "never received an event"
        print(f"  {p['id']}  {p.get('name', '?')}\n      last event: {fired}")


if __name__ == "__main__":
    main()
