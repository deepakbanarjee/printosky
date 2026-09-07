# -*- coding: utf-8 -*-
"""Instagram Automated Comment-to-DM Engine for Printosky (@printosky_official).

Monitors comments on recent Instagram posts and sends instant automated private
messages (DMs) and public confirmation replies based on keyword triggers:
- 'KTU'     -> Sends verified KTU/Calicut project report template + ₹20 print credit
- 'SKIP'    -> Sends WhatsApp instant order link with ₹20 queue-skip credit
- 'PRINT'   -> Sends WhatsApp order bot link
- 'ACCOUNT' -> Sends student VIP account activation link
- 'CALC'    -> Sends color slicing rate breakdown

Usage:
  python marketing/instagram_comment_bot.py --poll        # Single pass
  python marketing/instagram_comment_bot.py --watch       # Continuous daemon (every 30s)
  python marketing/instagram_comment_bot.py --test        # Dry-run test of triggers
"""

import argparse
import json
import os
import sys
import time
import requests

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

GRAPH_VERSION = "v20.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
PROCESSED_FILE = os.path.join(DATA_DIR, "processed_comments.json")
os.makedirs(DATA_DIR, exist_ok=True)

# ── Trigger Rules ─────────────────────────────────────────────────────────────
TRIGGERS = {
    "KTU": {
        "keywords": ["ktu", "template", "format", "margin", "calicut"],
        "public_reply": "Sent you the verified KTU project template in your DM! 📄✨",
        "dm_message": (
            "Hey! 👋 Here is your verified KTU & Calicut University Project Report Template (Word & LaTeX) "
            "with pre-configured margins and fonts:\n\n"
            "🔗 https://printosky.com/campus.html\n\n"
            "⚡ When you're ready to print, send your PDF to our WhatsApp bot to skip the queue "
            "and claim ₹20 welcome credit:\n"
            "👉 https://wa.me/919495706405?text=Hey+Printosky!+Ready+to+print+my+project"
        ),
    },
    "SKIP": {
        "keywords": ["skip", "print", "queue", "xerox", "order", "credit"],
        "public_reply": "Check your DMs! Your ₹20 queue-skip credit is waiting ⚡",
        "dm_message": (
            "Hey! 👋 Skip standing in the counter queue. Send your PDF to our WhatsApp bot "
            "from your hostel or college bus, and your printout will be waiting for 30-second pickup!\n\n"
            "🎁 Your ₹20 store credit has been pre-applied:\n"
            "👉 https://wa.me/919495706405?text=Hey+Printosky!+Claiming+my+Rs.20+credit"
        ),
    },
    "ACCOUNT": {
        "keywords": ["account", "vip", "student", "discount", "perks"],
        "public_reply": "Sent you the Student Account activation link in DMs! 🎓",
        "dm_message": (
            "Hey! 🎓 Opening a free Printosky Student Account locks in our lowest rates:\n"
            "• A4 B&W at ₹1.50 / sheet (~75 paise per page DS!)\n"
            "• A4 Colour at ₹8.00 flat from sheet 1\n"
            "• Counter queue priority\n\n"
            "Activate your account in 10 seconds on WhatsApp:\n"
            "👉 https://wa.me/919495706405?text=ACCOUNT"
        ),
    },
    "CALC": {
        "keywords": ["calc", "rate", "cost", "price", "slicing"],
        "public_reply": "Sent you our Smart Slicing rate card & savings calculator! 💰",
        "dm_message": (
            "Hey! 📊 Printosky automatically inspects every PDF page to slice B&W text from genuine color graphs. "
            "You only pay color rates for color pages!\n\n"
            "Calculate your exact thesis printing cost here:\n"
            "👉 https://printosky.com/campus.html\n\n"
            "Or send your PDF directly to WhatsApp for an instant automated quote: 9495 706 405"
        ),
    },
    "BIND": {
        "keywords": ["bind", "binding", "hardbound", "spiral", "foil", "thesis"],
        "public_reply": "Sent you our official Thesis Binding rates & options in DMs! 📚✨",
        "dm_message": (
            "Hey! 📚 Here are Printosky's official project binding options & rates:\n\n"
            "• Spiral & Twin-Loop Wiro (360° lay-flat): ₹30–₹50\n"
            "• Soft Thermal Paperback Binding: ₹60 flat\n"
            "• Deluxe Gold/Silver Foil Stamped Hardbound: ₹220 (standard) / ₹250 (deluxe foil)\n\n"
            "⚡ Next-Day Pickup Guarantee: Send your approved PDF to WhatsApp before 8:00 PM:\n"
            "👉 https://wa.me/919495706405?text=Hey+Printosky!+I+need+project+binding"
        ),
    },
}


def load_credentials():
    env_path = ".env"
    creds = {}
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    creds[k.strip()] = v.strip().strip('"').strip("'")

    token = creds.get("INSTAGRAM_PAGE_ACCESS_TOKEN")
    account_id = creds.get("INSTAGRAM_ACCOUNT_ID")
    page_id = creds.get("META_PAGE_ID")
    return token, account_id, page_id


def load_processed_ids():
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_processed_ids(processed_set):
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(list(processed_set), f, indent=2)


def match_trigger(comment_text):
    text = comment_text.lower().strip()
    for trigger_key, rule in TRIGGERS.items():
        for kw in rule["keywords"]:
            if kw in text:
                return trigger_key, rule
    # Default fallback to SKIP / print credit if they comment anything positive
    return "SKIP", TRIGGERS["SKIP"]


def send_private_dm(token, account_id, comment_id, message_text):
    """Send private DM reply to commenter using Meta Private Reply API."""
    url = f"{BASE_URL}/{account_id}/messages"
    payload = {
        "recipient": json.dumps({"comment_id": comment_id}),
        "message": json.dumps({"text": message_text}),
        "access_token": token,
    }
    r = requests.post(url, data=payload)
    res = r.json()
    if "error" in res:
        # Fallback to comment-level private_replies endpoint
        fb_url = f"{BASE_URL}/{comment_id}/private_replies"
        fb_r = requests.post(fb_url, data={"message": message_text, "access_token": token})
        fb_res = fb_r.json()
        if "error" in fb_res:
            print(f"    [!] DM error: {fb_res['error'].get('message')}")
            return False
    return True


def send_public_reply(token, comment_id, reply_text):
    """Post a public reply to acknowledge the comment."""
    url = f"{BASE_URL}/{comment_id}/replies"
    payload = {
        "message": reply_text,
        "access_token": token,
    }
    r = requests.post(url, data=payload)
    res = r.json()
    if "error" in res:
        print(f"    [!] Public reply error: {res['error'].get('message')}")
        return False
    return True


def poll_comments(token, account_id, processed_ids, dry_run=False):
    """Fetch recent posts and process any new comments."""
    media_url = f"{BASE_URL}/{account_id}/media"
    params = {
        "fields": "id,caption,comments{id,text,from,timestamp}",
        "limit": "10",
        "access_token": token,
    }
    r = requests.get(media_url, params=params)
    res = r.json()

    if "error" in res:
        print(f"[!] Error fetching media: {res['error'].get('message')}")
        return 0

    posts = res.get("data", [])
    new_handled = 0

    for post in posts:
        post_id = post.get("id")
        comments_obj = post.get("comments", {})
        comments = comments_obj.get("data", [])

        for c in comments:
            cid = c.get("id")
            if cid in processed_ids:
                continue

            comment_text = c.get("text", "")
            username = c.get("from", {}).get("username", "student")

            trigger_name, rule = match_trigger(comment_text)
            print(f"[NEW COMMENT] from @{username}: \"{comment_text}\" -> Trigger: {trigger_name}")

            if not dry_run:
                dm_ok = send_private_dm(token, account_id, cid, rule["dm_message"])
                if dm_ok:
                    print(f"  [OK] Sent DM to @{username}")
                pub_ok = send_public_reply(token, cid, rule["public_reply"])
                if pub_ok:
                    print(f"  [OK] Posted public reply to @{username}")

            processed_ids.add(cid)
            new_handled += 1

    save_processed_ids(processed_ids)
    return new_handled


def main():
    parser = argparse.ArgumentParser(description="Instagram Comment-to-DM Bot for Printosky")
    parser.add_argument("--poll", action="store_true", help="Run a single pass to check and reply to new comments")
    parser.add_argument("--watch", action="store_true", help="Run continuously in background (checks every 30 seconds)")
    parser.add_argument("--test", action="store_true", help="Test trigger keyword matching offline")
    args = parser.parse_args()

    if args.test:
        test_phrases = [
            "Please send the KTU project template",
            "How to skip the queue?",
            "Can I get a student account?",
            "What is the cost for color pages?",
            "Awesome post!",
        ]
        print("=== Testing Comment Trigger Rules ===")
        for p in test_phrases:
            trig, rule = match_trigger(p)
            print(f"Input : \"{p}\"")
            print(f"Trigger: {trig}")
            print(f"Reply  : {rule['public_reply']}\n")
        return

    token, account_id, page_id = load_credentials()
    processed_ids = load_processed_ids()
    print("================================================================")
    print(" PRINTOSKY COMMENT-TO-DM ENGINE RUNNING")
    print(f" Account: @printosky_official (ID: {account_id})")
    print(f" Processed comments in database: {len(processed_ids)}")
    print("================================================================")

    if args.watch:
        print("Watching for new comments every 30 seconds... (Press Ctrl+C to stop)")
        try:
            while True:
                handled = poll_comments(token, account_id, processed_ids)
                if handled > 0:
                    print(f"[{time.strftime('%H:%M:%S')}] Handled {handled} new comments.")
                time.sleep(30)
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        handled = poll_comments(token, account_id, processed_ids)
        print(f"[DONE] Handled {handled} new comments.")


if __name__ == "__main__":
    main()
