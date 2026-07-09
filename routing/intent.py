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


_PRINT_WORDS = {
    "print", "printout", "printing", "xerox", "photocopy", "photostat",
    "scan", "scanning",
}
_ACADEMIC_WORDS = {
    "project", "projectreport", "dissertation", "thesis", "assignment",
    "seminar", "binding", "spiral",
}
_NOTES_WORDS = {"notes"}


def keyword_intent(text: str) -> str | None:
    """Cheap deterministic hints for the obvious cases. None ⇒ ask the LLM.

    Sociology and books reuse book_bot's existing trigger matchers so we stay in
    sync with the flows they open. Book intents map to 'xtraa' in Plan 1 (the
    shared catalog); Plan 2 introduces the malayalam split.
    """
    if not text:
        return None
    t = text.strip().lower()
    words = set(t.replace("-", "").split())

    # Sociology is the most specific — check it before the generic book trigger.
    try:
        from book_bot import is_soc_trigger
        if is_soc_trigger(t):
            return "sociology"
    except Exception:
        pass

    if words & _PRINT_WORDS:
        return "print"
    if words & _ACADEMIC_WORDS:
        return "academic"
    if words & _NOTES_WORDS:
        return "notes"

    try:
        from book_bot import is_book_trigger
        if is_book_trigger(t):
            return "xtraa"
    except Exception:
        pass
    return None


import os
import time

MODEL = "claude-haiku-4-5"
CONFIDENCE_THRESHOLD = 0.6

# Haiku list price (USD / 1M tokens) — telemetry only; mirrors anu_parser.
_HAIKU_IN_USD_PER_1M = 1.0
_HAIKU_OUT_USD_PER_1M = 5.0
_USD_INR = 83.0

_CLASSIFY_TOOL = {
    "name": "route",
    "description": "Classify what the customer wants from the print shop.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": list(INTENTS) + ["unknown"],
                "description": (
                    "print = print/xerox/scan a file or document. "
                    "xtraa = English or Hindi learning books (Easy English, Vidyamrut). "
                    "malayalam = the Malayalam book Aksharamrutham. "
                    "sociology = MA Sociology / SNGU university books. "
                    "academic = a college project report / dissertation / binding. "
                    "notes = buy or sell study notes. "
                    "unknown = greeting, unclear, or none of the above."
                ),
            },
            "confidence": {
                "type": "number",
                "description": "0.0-1.0 confidence in the chosen intent.",
            },
        },
        "required": ["intent", "confidence"],
    },
}

_CLASSIFY_PROMPT = """You are the front desk of a print shop in Thrissur, Kerala.
Read ONE customer WhatsApp message (often a mix of English and Malayalam, or
Malayalam typed in English letters / "Manglish") and classify what they want.
Pick exactly one intent and a confidence. If it is just a greeting or unclear,
use intent="unknown". Record your answer via the route tool."""


def _record_cost(msg, elapsed_ms: int) -> None:
    try:
        in_tok = int(getattr(msg.usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(msg.usage, "output_tokens", 0) or 0)
        cost_usd = (in_tok * _HAIKU_IN_USD_PER_1M + out_tok * _HAIKU_OUT_USD_PER_1M) / 1_000_000
        import db_cloud
        db_cloud.log_llm_cost("intent_router", MODEL, in_tok, out_tok,
                              cost_usd, cost_usd * _USD_INR, elapsed_ms)
    except Exception as exc:
        logger.warning("intent_router cost logging failed: %s", exc)


def classify_intent(text: str) -> tuple[str, float]:
    """Claude Haiku intent classification. Never raises → ('unknown', 0.0) on any failure."""
    if not text or not text.strip():
        return ("unknown", 0.0)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("intent_router: ANTHROPIC_API_KEY missing; cannot classify")
        return ("unknown", 0.0)
    t0 = time.monotonic()
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=MODEL,
            max_tokens=256,
            tools=[_CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "route"},
            messages=[{"role": "user", "content": _CLASSIFY_PROMPT + "\n\nMESSAGE:\n" + text}],
        )
        _record_cost(msg, int((time.monotonic() - t0) * 1000))
        for block in msg.content:
            if block.type == "tool_use":
                data = dict(block.input)
                intent = str(data.get("intent", "unknown"))
                try:
                    conf = float(data.get("confidence", 0.0))
                except (TypeError, ValueError):
                    conf = 0.0
                return (intent, conf)
        return ("unknown", 0.0)
    except Exception as exc:
        logger.error("intent_router.classify_intent error: %s", exc)
        return ("unknown", 0.0)


def decide_intent(text: str, classifier=None) -> str:
    """Chain the layers: tag → keyword → LLM → 'unknown'.

    `classifier` is injectable for tests; defaults to the real Haiku call.
    Returns a value in INTENTS, or 'unknown' (⇒ show the menu).
    """
    tag = parse_intent_tag(text)
    if tag:
        return tag
    kw = keyword_intent(text)
    if kw:
        return kw
    classifier = classifier or classify_intent
    intent, conf = classifier(text)
    if intent in INTENTS and conf >= CONFIDENCE_THRESHOLD:
        return intent
    return "unknown"
