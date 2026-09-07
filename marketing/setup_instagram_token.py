# -*- coding: utf-8 -*-
"""Local Instagram Permanent Token Exchanger for Printosky.

Safely exchanges a short-lived token for a permanent Page Access Token
without printing secrets to the screen or sending them over chat.

Usage:
  python marketing/setup_instagram_token.py
"""

import os
import sys
import getpass
import requests

PAGE_ID = "999350999936953"
IG_ACCOUNT_ID = "17841468855448471"
APP_ID = "1650967860001560"

def load_env_dict():
    env_path = ".env"
    creds = {}
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    parts = line.split("=", 1)
                    k = parts[0].strip()
                    v = parts[1].strip().strip('"').strip("'")
                    creds[k] = v
    return creds

def save_to_env(updates):
    env_path = ".env"
    existing_lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
            existing_lines = f.readlines()

    keys_to_update = set(updates.keys())
    new_lines = []
    updated_keys = set()

    for line in existing_lines:
        trimmed = line.strip()
        matched = False
        for k in keys_to_update:
            if trimmed.startswith(f"{k}=") or trimmed.startswith(f"{k} ="):
                new_lines.append(f"{k}={updates[k]}\n")
                updated_keys.add(k)
                matched = True
                break
        if not matched:
            new_lines.append(line)

    for k, v in updates.items():
        if k not in updated_keys:
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines.append("\n")
            new_lines.append(f"{k}={v}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    print(f"[OK] Successfully updated {list(updates.keys())} in .env")

def main():
    print("================================================================")
    print(" PRINTOSKY INSTAGRAM PERMANENT TOKEN EXCHANGER")
    print("================================================================")
    creds = load_env_dict()

    app_id = creds.get("META_INSTAGRAM_APP_ID", APP_ID)
    app_secret = creds.get("META_INSTAGRAM_APP_SECRET")
    short_token = creds.get("META_SHORT_TOKEN")

    if not app_secret:
        print("\nYour App Secret was not found in .env.")
        print("Please paste your Meta App Secret below (input will be hidden):")
        app_secret = getpass.getpass("App Secret: ").strip()

    if not app_secret:
        print("Error: App Secret is required.")
        sys.exit(1)

    if not short_token:
        # Never hardcode this. A short-lived token committed here reached a
        # public repo on 2026-09-07; it is a credential like any other, and a
        # literal in a tracked file is a leak the moment it is pushed. Prompt
        # for it exactly as the App Secret above is prompted for.
        print("")
        print("No META_SHORT_TOKEN found in .env.")
        print("Generate one in Graph API Explorer and paste it below "
              "(input will be hidden):")
        short_token = getpass.getpass("Short-lived token: ").strip()

    if not short_token:
        print("Error: a short-lived token is required.")
        sys.exit(1)

    print("\n[1/3] Exchanging short-lived token for 60-day Long-Lived User Token...")
    url = (
        f"https://graph.facebook.com/v20.0/oauth/access_token?"
        f"grant_type=fb_exchange_token&client_id={app_id}&client_secret={app_secret}"
        f"&fb_exchange_token={short_token}"
    )
    r = requests.get(url)
    res = r.json()
    if "error" in res:
        print(f"[FAIL] Error exchanging token: {res['error']['message']}")
        sys.exit(1)

    long_user_token = res["access_token"]
    print("[OK] Long-lived user token acquired!")

    print("\n[2/3] Generating Permanent Never-Expiring Page Token for Printosky.com...")
    r_page = requests.get(f"https://graph.facebook.com/v20.0/{PAGE_ID}?fields=access_token&access_token={long_user_token}")
    res_page = r_page.json()
    if "error" in res_page:
        print(f"[FAIL] Error getting page token: {res_page['error']['message']}")
        sys.exit(1)

    permanent_page_token = res_page["access_token"]
    print("[OK] Permanent Page Access Token generated!")

    print("\n[3/3] Verifying Instagram Account (@printosky_official)...")
    r_ig = requests.get(
        f"https://graph.facebook.com/v20.0/{IG_ACCOUNT_ID}?"
        f"fields=id,username,name,followers_count,media_count&access_token={permanent_page_token}"
    )
    res_ig = r_ig.json()
    if "error" in res_ig:
        print(f"[FAIL] Instagram verification failed: {res_ig['error']['message']}")
        sys.exit(1)

    print(f"[SUCCESS] Connected to Instagram: @{res_ig.get('username')} (ID: {res_ig.get('id')})")
    print(f"          Followers: {res_ig.get('followers_count')} | Media Count: {res_ig.get('media_count')}")

    updates = {
        "META_INSTAGRAM_APP_ID": app_id,
        "META_INSTAGRAM_APP_SECRET": app_secret,
        "META_PAGE_ID": PAGE_ID,
        "INSTAGRAM_ACCOUNT_ID": IG_ACCOUNT_ID,
        "INSTAGRAM_PAGE_ACCESS_TOKEN": permanent_page_token,
    }
    save_to_env(updates)
    print("\n[DONE] All credentials safely stored in .env! You're ready to automate.")

if __name__ == "__main__":
    main()
