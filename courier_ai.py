"""AI + matching helpers for courier workflows.

Runs on Vercel, where ANTHROPIC_API_KEY is configured and the `anthropic` SDK is
installed (api/requirements.txt) — the same setup docx_engine / anu_parser use.
Every AI call FAILS SOFT: on a missing key or any error the caller gets the
originals (transliteration) or an empty list (manifest parse) back, so a courier
slip render or a manifest import never hard-crashes.

Two features:
  * transliterate_fields  — Malayalam name/address -> English for the courier slip.
  * parse_manifest        — India Post bulk-booking receipt (PDF/photo) -> rows.
  * match_rows            — match parsed rows to orders by PIN + name (no AI).
"""
import base64
import difflib
import logging
import os
import re

logger = logging.getLogger("api.webhook")

# Both tasks use Sonnet. Transliteration: Haiku drops trailing vowels (അനില ->
# "Anil", അജിത -> "Ajith"); Sonnet gets Kerala names right. Vision parse: Haiku
# silently dropped rows even on a clean tabular receipt (read 3 of 5), so Sonnet is
# the safe default — a missed row = a parcel that never gets its tracking. Both
# env-overridable; cost is a few rupees/manifest either way at this volume.
TRANSLITERATE_MODEL = os.environ.get("COURIER_TRANSLITERATE_MODEL", "claude-sonnet-4-6")
VISION_MODEL = os.environ.get("COURIER_VISION_MODEL", "claude-sonnet-4-6")

_ML_START, _ML_END = "ഀ", "ൿ"  # Malayalam Unicode block


def has_malayalam(s: str | None) -> bool:
    """True if the string contains any Malayalam-script character."""
    return any(_ML_START <= c <= _ML_END for c in (s or ""))


def _client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        logger.warning("courier_ai: ANTHROPIC_API_KEY missing; skipping AI call")
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=key)
    except Exception as exc:
        logger.error("courier_ai: anthropic init failed: %s", exc)
        return None


# ── Feature 2: English courier slip ─────────────────────────────────────────
def transliterate_fields(values: list[str]) -> list[str]:
    """Transliterate any Malayalam entries in `values` to readable English for a
    courier label, batched into one call. Preserves English text, digits, PINs and
    punctuation. Returns a same-length list; on any failure returns inputs unchanged.
    """
    values = list(values)
    targets = [i for i, v in enumerate(values) if has_malayalam(v)]
    if not targets:
        return values
    client = _client()
    if client is None:
        return values
    tool = {
        "name": "transliterated",
        "description": "English transliteration for each supplied item.",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "english": {"type": "string"},
                        },
                        "required": ["id", "english"],
                    },
                },
            },
            "required": ["items"],
        },
    }
    listing = "\n".join(f"{i}: {values[i]}" for i in targets)
    prompt = (
        "Transliterate each line from Malayalam script into English (Latin) for a "
        "courier address label. Read names letter by letter and PRESERVE EVERY "
        "VOWEL faithfully — do not drop or shorten trailing vowels or syllables "
        "(അനില -> Anila, not Anil; അജിത -> Ajitha, not Ajith). Use the standard "
        "English spelling of Kerala names/places (സജിത -> Sajitha, "
        "തൃശൂർ -> Thrissur, കേരളം -> Kerala). Keep any English text, digits, PIN "
        "codes and punctuation exactly as-is. Convert script only; do not translate "
        "meaning. Each line is `<id>: <text>`; return one result per id.\n\n" + listing
    )
    try:
        msg = client.messages.create(
            model=TRANSLITERATE_MODEL, max_tokens=1024, tools=[tool],
            tool_choice={"type": "tool", "name": "transliterated"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in msg.content:
            if getattr(block, "type", None) == "tool_use":
                for it in block.input.get("items", []):
                    i = it.get("id")
                    eng = (it.get("english") or "").strip()
                    if isinstance(i, int) and 0 <= i < len(values) and eng:
                        values[i] = eng
        return values
    except Exception as exc:
        logger.error("transliterate_fields error: %s", exc)
        return values


# ── Feature 1: courier manifest import ──────────────────────────────────────
def parse_manifest(file_bytes: bytes, mime_type: str) -> dict:
    """Extract parcel rows from a courier bulk-booking receipt (India Post PDF or
    photo) via Claude vision. Returns
    {"rows": [{article_number, receiver_name, dest_pin}], "stated_count": int|None}
    where stated_count is the total the receipt itself prints (Grand Total Qty /
    No. of Articles) — used to warn the operator when rows were missed. On any
    failure returns {"rows": [], "stated_count": None}. Fails soft.
    """
    client = _client()
    if client is None or not file_bytes:
        return {"rows": [], "stated_count": None}
    b64 = base64.standard_b64encode(file_bytes).decode("ascii")
    if mime_type == "application/pdf":
        source_block = {"type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
    else:
        source_block = {"type": "image",
                        "source": {"type": "base64", "media_type": mime_type, "data": b64}}
    tool = {
        "name": "manifest_rows",
        "description": "Parcel rows extracted from the courier booking receipt.",
        "input_schema": {
            "type": "object",
            "properties": {
                "stated_count": {"type": "integer",
                                 "description": "Total parcels/articles the receipt itself prints (Grand Total Qty, or 'No. of Articles'); 0 if none is shown"},
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "article_number": {"type": "string",
                                               "description": "Tracking / consignment / article number"},
                            "receiver_name": {"type": "string",
                                              "description": "Receiver / consignee name"},
                            "dest_pin": {"type": "string",
                                         "description": "Destination PIN code, 6 digits"},
                        },
                        "required": ["article_number", "receiver_name", "dest_pin"],
                    },
                },
            },
            "required": ["rows"],
        },
    }
    prompt = (
        "This is an India Post booking record for parcels. It may be EITHER a "
        "single tabular 'Bulk Booking Receipt' with one row per parcel, OR a photo "
        "of several individual counter receipts laid out on one page (text may be "
        "rotated or torn).\n\n"
        "Extract EVERY parcel — go row by row through the whole table/page and do "
        "NOT skip, merge, or stop early. A receipt can have 5, 10, 20 or more rows; "
        "return them all.\n\n"
        "For each parcel return:\n"
        "- article_number: the consignment/tracking number, format like CL622881144IN\n"
        "- receiver_name: the addressee after 'To:' / in the Receiver column — NEVER "
        "the sender (From: PRINTOSKY is the sender, ignore it)\n"
        "- dest_pin: the 6-digit destination PIN code in the receiver's address\n\n"
        "Also return stated_count: the total number of parcels the receipt itself "
        "prints (e.g. the 'Grand Total' quantity, or 'No. of Articles'). Your rows "
        "array MUST contain that many entries. Use 0 if no total is shown.\n\n"
        "Ignore the sender, office name, dates, amounts, and all tax / summary "
        "blocks. If a value is unreadable use an empty string."
    )
    try:
        msg = client.messages.create(
            model=VISION_MODEL, max_tokens=4096, tools=[tool],
            tool_choice={"type": "tool", "name": "manifest_rows"},
            messages=[{"role": "user",
                       "content": [source_block, {"type": "text", "text": prompt}]}],
        )
        for block in msg.content:
            if getattr(block, "type", None) == "tool_use":
                rows = [r for r in block.input.get("rows", []) if isinstance(r, dict)]
                sc = block.input.get("stated_count")
                stated = int(sc) if isinstance(sc, int) and sc > 0 else None
                return {"rows": rows, "stated_count": stated}
        return {"rows": [], "stated_count": None}
    except Exception as exc:
        logger.error("parse_manifest error: %s", exc)
        return {"rows": [], "stated_count": None}


def _extract_pin(text: str | None) -> str | None:
    """Last 6-digit run in the text (the PIN usually trails a delivery address)."""
    runs = re.findall(r"\d{6}", text or "")
    return runs[-1] if runs else None


def _norm_name(s: str | None) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).strip()


def match_rows(rows: list[dict], orders: list[dict]) -> dict:
    """Match manifest rows to orders by destination PIN, disambiguating by name.

    India Post manifests carry no phone, so PIN (from the order's address) is the
    join key; when several orders share a PIN, a strong name-similarity margin
    decides, otherwise the row is left 'ambiguous' for the operator to pick.
    Returns {matched:[{row,order,tracking}], ambiguous:[{row,candidates,tracking}],
    unmatched:[{row,tracking}]}.
    """
    by_pin: dict[str, list[dict]] = {}
    for o in orders:
        pin = _extract_pin(o.get("address") or "")
        if pin:
            by_pin.setdefault(pin, []).append(o)

    matched, ambiguous, unmatched = [], [], []
    for r in rows:
        tracking = (r.get("article_number") or "").strip()
        pin = _extract_pin(r.get("dest_pin")) or (r.get("dest_pin") or "").strip()
        cands = by_pin.get(pin, [])
        rname = _norm_name(r.get("receiver_name"))
        if len(cands) == 1:
            matched.append({"row": r, "order": cands[0], "tracking": tracking})
        elif len(cands) > 1:
            scored = sorted(
                ((difflib.SequenceMatcher(None, rname, _norm_name(o.get("name"))).ratio()
                  if rname and _norm_name(o.get("name")) else 0.0, o) for o in cands),
                key=lambda x: x[0], reverse=True,
            )
            top = scored[0][0]
            runner = scored[1][0] if len(scored) > 1 else 0.0
            if top >= 0.6 and (top - runner) >= 0.2:
                matched.append({"row": r, "order": scored[0][1], "tracking": tracking})
            else:
                ambiguous.append({"row": r, "candidates": cands, "tracking": tracking})
        else:
            unmatched.append({"row": r, "tracking": tracking})
    return {"matched": matched, "ambiguous": ambiguous, "unmatched": unmatched}
