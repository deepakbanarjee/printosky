"""Front-door intent detection for the shared WhatsApp line.

Four layers, cheapest first:
  1. parse_intent_tag(text) — deterministic. Landing-page deep-link tags
     (#print, #xtraa, …) and interactive-menu row ids (intent_print, …).
  2. keyword_intent(text)   — deterministic keyword hints (reuses book_bot triggers).
  3. classify_intent(text)  — Claude Haiku fallback for free-form messages.
  4. menu (unknown)         — build_menu_rows() for the tap-to-choose fallback.

decide_intent() chains 1→2→3→(unknown). route_front_door() performs the side
effect. No HTTP/webhook knowledge lives here.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Canonical intents the front door can route. "unknown" is NOT in this tuple —
# it is the explicit fall-through to the menu.
INTENTS: tuple[str, ...] = (
    "print", "xtraa", "malayalam", "sociology", "academic", "notes",
)

# Tag → intent. Covers hashtag tags emitted by landing-page deep links AND the
# row ids sent when a customer taps the fallback menu.
_TAG_MAP: dict[str, str] = {
    "#print": "print",         "intent_print": "print",
    "#xtraa": "xtraa",         "intent_xtraa": "xtraa",
    "#malayalam": "malayalam", "intent_malayalam": "malayalam",
    "#soc": "sociology",       "#sociology": "sociology", "intent_sociology": "sociology",
    "#academic": "academic",   "intent_academic": "academic",
    "#notes": "notes",         "intent_notes": "notes",
}


def parse_intent_tag(text: str) -> str | None:
    """Deterministic intent from a deep-link hashtag or a tapped menu row id."""
    if not text:
        return None
    t = text.strip().lower()
    if t in _TAG_MAP:                      # exact menu-id / bare tag
        return _TAG_MAP[t]
    for tag, intent in _TAG_MAP.items():   # hashtag anywhere in a free message
        if tag.startswith("#") and tag in t:
            return intent
    return None
