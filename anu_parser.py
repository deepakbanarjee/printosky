"""LLM extraction of a single Divya/Anu book order from a free-form forward.

Anu forwards the teacher's raw WhatsApp text (one order at a time, usually a
mix of Malayalam and English). We use Claude Haiku with forced tool-use to pull
structured fields, and — per owner decision — we DO NOT guess the book: when the
book isn't clearly named we flag `needs_clarification` so the bot asks Anu in chat.

Production (Vercel / store PC) has ANTHROPIC_API_KEY in the environment and no
proxy, so the standard client works. Returns {"is_order": False} on any failure
so the caller can degrade gracefully (never raises).
"""
import os
import time
import logging

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5"

# Claude Haiku 4.5 list price (USD per 1M tokens). Used only to estimate cost for
# telemetry — the real bill is on the Anthropic dashboard. Token counts are exact.
HAIKU_INPUT_USD_PER_1M = 1.0
HAIKU_OUTPUT_USD_PER_1M = 5.0
USD_INR = 83.0


def _record_cost(msg, elapsed_ms: int) -> None:
    """Log + persist the cost of one parse call (best-effort; never raises)."""
    try:
        in_tok = int(getattr(msg.usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(msg.usage, "output_tokens", 0) or 0)
        cost_usd = (in_tok * HAIKU_INPUT_USD_PER_1M
                    + out_tok * HAIKU_OUTPUT_USD_PER_1M) / 1_000_000
        cost_inr = cost_usd * USD_INR
        logger.info("anu_parser cost: in=%d out=%d -> $%.5f (~Rs%.3f) in %dms",
                    in_tok, out_tok, cost_usd, cost_inr, elapsed_ms)
        import db_cloud
        db_cloud.log_llm_cost("anu_parser", MODEL, in_tok, out_tok,
                              cost_usd, cost_inr, elapsed_ms)
    except Exception as exc:
        logger.warning("anu_parser cost logging failed: %s", exc)

# Canonical book keys ↔ titles (kept in sync with book_catalog.BOOKS).
BOOK_TITLES = {
    "malayalam": "Aksharamrutham",
    "hindi":     "Vidyamrut",
    "english":   "Easy English",
}

_TOOL = {
    "name": "record_order",
    "description": "Record the single book order in the forwarded message, or flag that it is not an order.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_order": {
                "type": "boolean",
                "description": "true only if this looks like a book order (a customer name plus a phone or postal address). false for casual chat, greetings, payment notes, or replies.",
            },
            "name": {"type": "string", "description": "customer name, cleaned of stray punctuation"},
            "phone": {"type": "string", "description": "10-digit Indian mobile, digits only"},
            "address": {"type": "string", "description": "full delivery address as ONE line (house, post, place, district) — exclude the name, phone and pincode"},
            "pincode": {"type": "string", "description": "6-digit PIN code if present"},
            "copies": {"type": "integer", "description": "number of copies requested (default 1 if a count like '1 Copy' is present)"},
            "books": {
                "type": "array",
                "description": "books explicitly named. Leave EMPTY if no specific book title was named.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "enum": ["malayalam", "hindi", "english"]},
                        "qty": {"type": "integer"},
                    },
                    "required": ["title", "qty"],
                },
            },
            "needs_clarification": {
                "type": "boolean",
                "description": "true if the book is NOT clearly named, or the titles/quantities are ambiguous (e.g. 'both books'), or the customer asked a question that affects the order.",
            },
            "clarification": {
                "type": "string",
                "description": "if needs_clarification, a short question to ask the forwarder (Anu), e.g. 'Which book for Bincy Mathew?'",
            },
        },
        "required": ["is_order"],
    },
}

_PROMPT = """You extract ONE book order from a WhatsApp message a teacher forwarded.
The text is often a mix of Malayalam and English and may include a leading list
number (e.g. "8."). Each order has a customer name, a postal address with a
6-digit PIN, a phone number (labelled "Mob", "Ph", "Phone", Malayalam phone word,
or bare), and a number of copies ("1 Copy", "I Copy" = 1 copy).

Books in this campaign (enum keys):
- "malayalam" = Aksharamrutham
- "hindi"     = Vidyamrut
- "english"   = Easy English

CRITICAL rules:
- Only set is_order=true if there is a customer name AND a phone or address.
- If a specific book title is clearly named, put it in `books` with the copy count.
- If NO specific book is named (the message just says e.g. "1 copy"), leave `books`
  EMPTY and set needs_clarification=true with a question asking which book. DO NOT guess.
- If the customer says something vague like "both books" / "രണ്ട് പുസ്തകങ്ങളും", leave
  `books` empty, set needs_clarification=true, and ask which two titles.
- If the customer asks a question (e.g. price), still extract the order and set
  needs_clarification=true noting the question.
- Always capture `copies` (default 1) even when the book is unknown.
- phone = 10 digits only. Record exactly one order via the record_order tool."""


def parse_order_message(text: str) -> dict:
    """Parse one forwarded order via Claude Haiku. Never raises.

    Returns the tool input dict (keys per _TOOL schema). On any error or when the
    message is not an order, returns a dict with is_order=False.
    """
    if not text or not text.strip():
        return {"is_order": False}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("anu_parser: ANTHROPIC_API_KEY missing; cannot LLM-parse")
        return {"is_order": False, "error": "no_api_key"}
    t0 = time.monotonic()
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "record_order"},
            messages=[{"role": "user", "content": _PROMPT + "\n\nMESSAGE:\n" + text}],
        )
        _record_cost(msg, int((time.monotonic() - t0) * 1000))
        for block in msg.content:
            if block.type == "tool_use":
                result = dict(block.input)
                result.setdefault("is_order", False)
                return result
        return {"is_order": False}
    except Exception as exc:
        logger.error("anu_parser.parse_order_message error: %s", exc)
        return {"is_order": False, "error": str(exc)}
