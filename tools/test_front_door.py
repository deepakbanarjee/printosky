#!/usr/bin/env python3
"""Manual dry-run for the WhatsApp smart front-door (Plan 1).

Type a customer message and see what the bot WOULD decide and send. Uses the
REAL Haiku classifier (needs ANTHROPIC_API_KEY in .env). Sends NOTHING to
WhatsApp and opens no real flow — it only prints the routing decision.

Usage:
  python tools/test_front_door.py                      # interactive
  python tools/test_front_door.py "malayalam book venam"   # one-shot
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # emoji in the menu titles need UTF-8 stdout on Windows consoles
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from routing import intent as ir

_ACTIONS = {
    "print":     "reply with PRINT link  -> https://printosky.com/order",
    "academic":  "reply with ACADEMIC link -> https://printosky.com/academic",
    "notes":     "reply with NOTES help text (upload / print note NOTE-XXXX)",
    "xtraa":     "open the Xtraa book catalog in chat",
    "malayalam": "open the shared book catalog in chat (interim; Plan 2 splits this out)",
    "sociology": "open the MA Sociology flow in chat",
    "unknown":   None,  # -> menu, filled in below
}


def _menu_line() -> str:
    rows = ", ".join(r["title"] for r in ir.build_menu_rows())
    return "show the tap-to-choose MENU -> " + rows


def run_one(msg: str) -> None:
    tag = ir.parse_intent_tag(msg)
    kw = ir.keyword_intent(msg)
    decided = ir.decide_intent(msg)
    layer = "tag" if tag else ("keyword" if kw else "classifier/none")
    action = _ACTIONS.get(decided) or _menu_line()
    print(f"  message : {msg!r}")
    print(f"  decided : {decided}   (via {layer})")
    print(f"  action  : {action}")
    print()


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("NOTE: ANTHROPIC_API_KEY not set — free-form messages will fall to "
              "the menu. Tags and keywords still work.\n")
    if len(sys.argv) > 1:
        run_one(" ".join(sys.argv[1:]))
        return
    print("Front-door dry-run. Type a customer message (blank line or Ctrl-C to quit).\n")
    try:
        while True:
            msg = input("customer> ").strip()
            if not msg:
                break
            run_one(msg)
    except (EOFError, KeyboardInterrupt):
        print()


if __name__ == "__main__":
    main()
