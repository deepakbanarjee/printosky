# -*- coding: utf-8 -*-
import datetime
import requests

def main():
    with open(".env", "r", encoding="utf-8", errors="ignore") as f:
        lines = f.read().splitlines()

    print("=== Meta / Instagram Keys in .env ===")
    token_val = None
    for l in lines:
        if "=" in l and not l.strip().startswith("#"):
            k, v = l.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if any(x in k for x in ["INSTA", "META_PAGE", "APP_SECRET", "SYSTEM_USER"]):
                print(f"  {k:<32} [length: {len(v)}]")
            if k == "INSTAGRAM_PAGE_ACCESS_TOKEN":
                token_val = v
            elif k == "META_INSTAGRAM_APP_SECRET":
                print(f"  --> Found App Secret! (length: {len(v)})")

    if not token_val:
        print("\n[!] No INSTAGRAM_PAGE_ACCESS_TOKEN found in .env.")
        return

    print("\n=== Validating Token with Meta Servers ===")
    url = f"https://graph.facebook.com/debug_token?input_token={token_val}&access_token={token_val}"
    r = requests.get(url)
    res = r.json()

    if "error" in res:
        print(f"[!] Error inspecting token: {res['error']['message']}")
        return

    d = res.get("data", {})
    print(f"  App Name     : {d.get('application')} (ID: {d.get('app_id')})")
    print(f"  Token Type   : {d.get('type')}")
    print(f"  Is Valid     : {d.get('is_valid')}")
    print(f"  Scopes       : {', '.join(d.get('scopes', []))}")

    exp = d.get("expires_at")
    print(f"  Raw Expire   : {exp}")
    if exp == 0:
        print("\n[SUCCESS] THIS TOKEN IS PERMANENT! (Expires: NEVER)")
    elif exp:
        dt = datetime.datetime.fromtimestamp(exp)
        now = datetime.datetime.now()
        remaining = dt - now
        print(f"\n[WARNING] Expires on: {dt} ({remaining.total_seconds() / 3600:.1f} hours remaining)")

    # Also test live query against @printosky_official
    account_id = "17841468855448471"
    ig_url = f"https://graph.facebook.com/v20.0/{account_id}?fields=id,username,followers_count&access_token={token_val}"
    ig_r = requests.get(ig_url).json()
    if "username" in ig_r:
        print(f"  Live Target  : @{ig_r['username']} (Followers: {ig_r.get('followers_count')})")

if __name__ == "__main__":
    main()
