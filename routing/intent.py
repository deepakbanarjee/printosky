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


_ORDER_LINK = "https://printosky.com/order"
_ACADEMIC_LINK = "https://printosky.com/academic"

_LINK_MESSAGES = {
    # WhatsApp first, the web form second (changed 2026-09-15). This used to
    # open with the order link, and the one ad arrival who ever reached it
    # (9 Sep) followed the link and never came back. The ad we pay for promises
    # "send your PDF on WhatsApp"; handing that person a web form is a second
    # queue in place of the one they were told they could skip. The link stays
    # for the people who want to pay online -- it is just no longer the first
    # thing a customer holding a PDF is asked to do.
    "print": (
        "🖨️ *Print a file*\n"
        "*Just send your PDF right here* — Word, PowerPoint and photos work "
        "too — and we'll quote you the exact price in a minute.\n\n"
        f"Prefer to pick paper / colour / copies and pay online? {_ORDER_LINK}\n\n"
        # Customer-facing copy is Printosky only (.agents/AGENTS.md). The
        # locality stays -- students want to know which counter to walk into.
        "— Printosky, Thriprayar"
    ),
    "academic": (
        "🎓 *Academic project*\n"
        "Submit your project-report details (and binding) here:\n"
        f"{_ACADEMIC_LINK}"
    ),
    "notes": (
        "📝 *Study notes*\n"
        "To sell your notes, reply *upload notes*.\n"
        "To buy a specific note, send *print note NOTE-XXXX*."
    ),
}


def build_menu_rows() -> list[dict]:
    """Rows for the tap-to-choose fallback menu (row id → parse_intent_tag).

    The three book rows disappear while sales are paused. Offering a button
    that answers "we can't sell you that" is a worse first impression than not
    offering it, and it is the menu three of five options come from.
    """
    rows = [
        {"id": "intent_print",     "title": "🖨️ Print a file",    "description": "Documents, photos, PDFs"},
        {"id": "intent_xtraa",     "title": "📘 Xtraa books",      "description": "English & Hindi learning books"},
        {"id": "intent_malayalam", "title": "📗 Malayalam book",   "description": "Aksharamrutham"},
        {"id": "intent_sociology", "title": "📕 Sociology books",  "description": "MA Sociology (SNGU)"},
        {"id": "intent_academic",  "title": "🎓 Academic project", "description": "Project report & binding"},
    ]
    try:
        from book_bot import books_paused
        if books_paused():
            book_ids = {"intent_xtraa", "intent_malayalam", "intent_sociology"}
            rows = [r for r in rows if r["id"] not in book_ids]
    except Exception as exc:
        # A menu is better than no menu; leaving the rows in is the status quo.
        logger.warning("books_paused check failed, showing full menu: %s", exc)
    return rows


# ── Ad arrivals ──────────────────────────────────────────────────────────────
# Someone who taps "Print your Thesis for Rs.0" and lands on the generic
# five-option menu has been answered by a different conversation than the one
# they started. Both real arrivals on 7 Sep did exactly that: replied "H", got
# the menu, went quiet. Neither typed MY CREDITS -- nothing on screen told them
# to. The ad is the first half of a conversation; this is the second half.
#
# Keyed by Meta ad id, because "what this ad offered" is not derivable from the
# referral payload -- Meta sent headline "Chat with us" for this campaign. A new
# campaign that wants its own welcome adds a line here; anything unlisted falls
# through to the ordinary menu rather than guessing.
#
# WHY THIS AD IS "quote" AND NOT "referral" (changed 2026-09-15)
# --------------------------------------------------------------
# It was "referral" for eight days. The measured result, over the whole run of
# ad 120248100283360186 (7-14 Sep): 8 clicks, 7 people, 7 of the 9 new contacts
# in that window -- and 0 print jobs, Rs.0 revenue, 0 referred orders. Five of
# the seven were handed a share link as the first thing they ever saw from us.
#
# The ad's own body is "Skip the 1-hour Xerox shop queue in Thrissur -- send
# your assignment, project report or thesis PDF on WhatsApp". Answering that
# with "recruit 15-20 classmates and your printing is free" asks a stranger who
# has never bought anything to vouch for us before we have done a single thing
# for them. The referral offer is not wrong, it is early: it belongs after the
# first delivered job, when they have something to vouch for.
#
# "referral" stays implemented, for an ad aimed at people who are already
# customers. It is simply not what a cold click-to-WhatsApp ad should hear.
AD_CAMPAIGN_KIND = {
    "120248100283360186": "quote",      # Skip the Xerox queue (Sep 2026)
}


def _rupees(value: float) -> str:
    """Money. Rs.2.00 reads as amateur next to Rs.30; Rs.1.5 reads as a bug."""
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _per_page(sheet_rate: float) -> str:
    """A per-sheet B&W rate said the way a student compares shops.

    Duplex puts two pages on a sheet and B&W is billed per sheet, so the page
    price is exactly half the sheet rate -- Rs.2 a sheet is Rs.1 a page, not
    the 50 paise it looks like at a glance. Derived, never typed: this is the
    number the customer checks us against.
    """
    per_page = sheet_rate / 2
    if per_page < 1:
        return f"{int(round(per_page * 100))} paise a page"
    return f"₹{_rupees(per_page)} a page"


def price_headlines() -> dict[str, str]:
    """The two or three numbers a student compares shops on, from rate_card.

    Read out of rate_card rather than retyped into the copy: a rate the owner
    changes must not leave a stale number sitting in an ad reply, which is the
    one message we pay Meta to deliver. Falls back to no numbers at all rather
    than to guessed ones -- an answer with no price still beats a wrong price.
    """
    try:
        from rate_card import (get_print_rate, PROJECT_BINDING_RATES,
                               SPIRAL_A4_TIERS, SOFT_BINDING_TIERS)
        # 50 sheets = a 100-page report printed double-sided, which is the
        # shape of nearly every job this ad is aimed at; 150 crosses into the
        # bulk tier.
        bw      = get_print_rate("A4_BW", "ds", 50, is_student=True)
        bw_bulk = get_print_rate("A4_BW", "ds", 150, is_student=True)
        return {
            "bw":      _rupees(bw),
            "bw_bulk": _rupees(bw_bulk),
            "per_page":      _per_page(bw),
            "per_page_bulk": _per_page(bw_bulk),
            # 50 sheets printed double-sided = the 100-page report this ad is
            # aimed at, priced end to end rather than left as an exercise.
            "report_100":    _rupees(round(bw * 50)),
            "colour":  _rupees(get_print_rate("A4_col", "ss", 60)),
            "spiral":  _rupees(SPIRAL_A4_TIERS[0][1]),
            "soft":    _rupees(SOFT_BINDING_TIERS[0][1]),
            "project": _rupees(min(PROJECT_BINDING_RATES.values())),
        }
    except Exception as exc:
        logger.error("price headlines unavailable, sending copy without them: %s", exc)
        return {}


def _send_quote_welcome(phone: str, name: str | None = None,
                        icebreaker: str | None = None) -> bool:
    """Answer a cold ad click with the thing the ad actually offered: a price.

    The ad says "send your PDF on WhatsApp and skip the queue". So this says
    the same, in the same thread, with a number attached -- and asks for the
    file rather than sending them to a web form. Every step between the tap and
    the PDF is a place the customer leaves; printosky.com/order is such a step,
    and the one arrival who reached it (9 Sep) did leave.

    `icebreaker` is the shop's answer to the question they tapped on the way in
    (see ICE_BREAKERS). When there is one it REPLACES the generic body: someone
    who asked "Where are you located?" gets the address, not a rate card that
    ignores them. It cannot be sent as a second message instead — the welcome
    and _services_answer/_price_answer say much the same thing, and two of those
    in a row reads as a bot talking to itself.

    Always returns True: unlike the referral welcome there is nothing to mint,
    so there is no failure mode that leaves the customer with silence.
    """
    p = price_headlines()
    hi = f"Hi {name}! " if name else "Hi! "
    if icebreaker:
        _send_text(phone, (
            f"{hi}👋 You tapped our *skip the Xerox queue* ad — you are in the "
            f"right place.\n\n"
            f"{icebreaker}"
        ))
        return True
    if p:
        rates = (
            f"📄 A4 B&W, student rate: *₹{p['bw']} a sheet* — printed "
            f"double-sided that is *{p['per_page']}*, so a 100-page report is "
            f"about ₹{p['report_100']} of printing "
            f"(₹{p['bw_bulk']} a sheet over 100).\n"
            f"🎨 A4 colour from *₹{p['colour']} a sheet* — and we slice out the "
            f"plain text pages automatically, so you only pay colour for the "
            f"pages that are actually colour.\n"
            f"📚 Binding: spiral from *₹{p['spiral']}*, soft from *₹{p['soft']}*, "
            f"hardbound project cover *₹{p['project']}*.\n\n"
        )
    else:
        rates = ""
    _send_text(phone, (
        f"{hi}👋 You tapped our *skip the Xerox queue* ad — you are in the "
        f"right place.\n\n"
        f"*Send your PDF right here in this chat* and we'll quote you the exact "
        f"price in a minute. No app, no signup, no queue. Word, PowerPoint and "
        f"photos work too.\n\n"
        f"{rates}"
        f"Ready when you are — just send the file. 🙏\n"
        f"— Printosky, Thriprayar"
    ))
    return True


def _send_referral_welcome(phone: str, name: str | None = None) -> bool:
    """Hand an ad arrival the share link the ad promised them.

    The ad tells people to text MY CREDITS to get this. We already know they
    came from the ad, so asking them to type a keyword first is a step that
    only loses people. Returns False if the code could not be minted, so the
    caller falls back to the menu rather than sending nothing.
    """
    from db_cloud import ensure_referral_code, referral_share_link
    code = ensure_referral_code(phone, platform="ad_click")
    if not code:
        return False        # ensure_referral_code has already logged the reason
    hi = f"Hi {name}! " if name else "Hi! "
    _send_text(phone, (
        f"{hi}👋 You're in — here's your *Printosky share link*:\n"
        f"{referral_share_link(code)}\n\n"
        f"Send it to classmates. Every friend who orders with it earns you "
        f"*₹20 store credit*, and 15–20 of them covers your semester printing "
        f"and final thesis for *₹0*.\n\n"
        f"Your code: *{code}*\n"
        f"Reply *MY CREDITS* any time to check your balance, or send a file "
        f"and we'll quote you straight away."
    ))
    return True


def pending_ad_arrival(phone: str) -> dict | None:
    """The ad click this phone is still owed a welcome for, or None.

    Public because the book flow has to ask it too. book_bot.maybe_handle_book
    runs ABOVE the front door in api/index._handle_text, and is_book_trigger
    matches the word "book" anywhere -- so on 19 Sep 2026 someone tapped the
    print ad, asked "Can I book an appointment?", and was sold the Malayalam
    book catalogue. The ad welcome never ran at all, and three hours later they
    got an abandoned-cart nudge for books they had never asked about.

    Never raises: this decides whether ANOTHER handler stands down, so an
    error here must leave the existing behaviour alone rather than silence a
    flow that works.
    """
    try:
        from db_cloud import ad_welcome_already_sent, recent_ad_click
        ad = recent_ad_click(phone)
        if not ad:
            return None
        if not AD_CAMPAIGN_KIND.get((ad.get("source_id") or "").strip()):
            return None
        if ad_welcome_already_sent(phone, ad.get("clicked_at")):
            return None
        return ad
    except Exception as exc:
        logger.warning("pending ad-arrival check failed for %s: %s", phone, exc)
        return None


def _maybe_welcome_ad_arrival(phone: str, name: str | None = None,
                              icebreaker: str | None = None) -> bool:
    """Answer the ad someone actually clicked. True if we handled the message.

    Fires on the first message after a click and never again -- see
    db_cloud.ad_welcome_already_sent for why that guard is a conversation_log
    lookup and not "do they hold a referral code".
    """
    try:
        ad = pending_ad_arrival(phone)
        if not ad:
            return False
        kind = AD_CAMPAIGN_KIND.get((ad.get("source_id") or "").strip())
        if kind == "quote":
            return _send_quote_welcome(phone, name, icebreaker=icebreaker)
        if kind == "referral":
            # A referral ad is aimed at people who already buy from us; someone
            # who holds a code has had this pitch and does not need it twice.
            from db_cloud import get_referral_code
            if get_referral_code(phone):
                return False
            return _send_referral_welcome(phone, name)
        logger.error("ad %s has kind %r with no handler — falling back to the menu",
                     ad.get("source_id"), kind)
        return False
    except Exception as exc:
        # Never cost the customer their reply over this: fall through to the
        # menu, which is what they would have got anyway.
        logger.warning("ad welcome failed for %s: %s", phone, exc)
        return False


# ── Meta ice-breakers ────────────────────────────────────────────────────────
# A click-to-WhatsApp ad shows a fixed set of tappable questions above the
# compose box. They are configured in Ads Manager, they arrive as ordinary text
# messages, and they are the FIRST thing most ad arrivals send -- three of the
# seven people who clicked ad 120248100283360186 opened with the verbatim
# string "What services do you offer?".
#
# All three were answered by a human, by hand, from the staff phone: asked at
# 04:12, 07:45 and 01:27 UTC, all three answered at 17:01-17:02 the same day.
# Nine hours, on a lead Meta charged us roughly Rs.147 for, and the answer when
# it came was "Printing and all types of binding" -- sent twice. The shop knows
# the answer to these questions; there is no reason a person has to be awake
# for it.
#
# Keep this list in step with the ice-breakers configured on the ad. Anything
# not listed falls through to the ordinary intent layers, which is where a
# free-form question already goes -- an unlisted ice-breaker is answered no
# worse than it is today, so a stale list costs nothing.
_ICE_BREAKER_PUNCTUATION = " ?!.।"


def _normalise_question(text: str) -> str:
    return " ".join((text or "").lower().split()).strip(_ICE_BREAKER_PUNCTUATION)


def _services_answer() -> str:
    p = price_headlines()
    rates = (
        f"📄 *Printing / photocopy* — A4 B&W student rate ₹{p['bw']} a sheet "
        f"(₹{p['bw_bulk']} over 100). Double-sided, that is {p['per_page']}.\n"
        f"🎨 *Colour* — from ₹{p['colour']} a sheet, and we charge colour only "
        f"for the pages that are actually colour.\n"
        f"📚 *Binding* — spiral from ₹{p['spiral']}, soft from ₹{p['soft']}, "
        f"hardbound project cover ₹{p['project']}.\n"
        f"🎓 *Project reports & thesis* — printed, bound and ready to submit.\n\n"
    ) if p else (
        "📄 Printing and photocopying, 🎨 colour, 📚 every kind of binding, "
        "🎓 project reports and thesis work.\n\n"
    )
    return (
        "Here's what we do 👇\n\n"
        f"{rates}"
        "*Send your PDF right here* and we'll quote you the exact price in a "
        "minute — no app, no signup.\n"
        "— Printosky, Thriprayar"
    )


def _price_answer() -> str:
    p = price_headlines()
    if not p:
        return ("Send your PDF right here and we'll quote you the exact price "
                "in a minute — it depends on pages, colour and binding.\n"
                "— Printosky, Thriprayar")
    return (
        "Our student rates 👇\n\n"
        f"📄 A4 B&W — *₹{p['bw']} a sheet*, ₹{p['bw_bulk']} over 100 sheets. "
        f"Printed double-sided that is *{p['per_page']}* ({p['per_page_bulk']} "
        f"over 100), so a 100-page report is about "
        f"*₹{p['report_100']}* of printing.\n"
        f"🎨 A4 colour — from *₹{p['colour']} a sheet*. Plain text pages inside "
        f"a colour document are billed as B&W automatically.\n"
        f"📚 Binding — spiral from *₹{p['spiral']}*, soft from *₹{p['soft']}*, "
        f"hardbound project cover *₹{p['project']}*.\n\n"
        "*Send your file here* for the exact number on your document. 🙏\n"
        "— Printosky, Thriprayar"
    )


def _location_answer() -> str:
    return (
        "📍 We're at *Thriprayar, Thrissur*.\n\n"
        "You don't have to come in to order, though — *send your PDF right "
        "here*, we'll quote you, print it, and tell you when it's ready to "
        "collect. 🙏\n"
        "— Printosky, Thriprayar"
    )


def _how_to_order_answer() -> str:
    return (
        "It's one step 👇\n\n"
        "*Send your PDF (or Word file, or photos) right here in this chat.*\n\n"
        "We'll reply with the exact price, you confirm, we print. No app, no "
        "signup, no queue. 🙏\n"
        "— Printosky, Thriprayar"
    )


ICE_BREAKERS: dict[str, "callable"] = {
    # Verified live on ad 120248100283360186 — three arrivals sent it verbatim.
    "what services do you offer": _services_answer,
    "what do you offer": _services_answer,
    "what services do you provide": _services_answer,
    "services": _services_answer,

    "what are your prices": _price_answer,
    "what is the price": _price_answer,
    "how much does it cost": _price_answer,
    "price": _price_answer,
    "rate": _price_answer,
    "rates": _price_answer,

    "where are you located": _location_answer,
    "where is your shop": _location_answer,
    "location": _location_answer,

    "how do i order": _how_to_order_answer,
    "how can i order": _how_to_order_answer,
    "how does it work": _how_to_order_answer,
}


def icebreaker_reply(text: str) -> str | None:
    """The shop's own answer to a tapped ad ice-breaker. None ⇒ not one."""
    builder = ICE_BREAKERS.get(_normalise_question(text))
    return builder() if builder else None


def _send_text(phone: str, message: str) -> None:
    from whatsapp_notify import _send
    _send(phone, message)


def _send_menu(phone: str) -> None:
    from whatsapp_notify import send_list
    ok = send_list(
        phone,
        body="Hi! 👋 How can we help you today? Tap what you need 👇",
        button_text="Choose",
        rows=build_menu_rows(),
        header="Printosky",
        section_title="How can we help",
    )
    if not ok:
        _send_text(phone, "How can we help?\n"
                          "• Print a file: send your PDF here, or "
                          + _ORDER_LINK + "\n"
                          "• Books: reply *books*\n"
                          "• Sociology: reply *sociology*\n"
                          "• Academic project: " + _ACADEMIC_LINK)


def _open_books(phone: str, name: str | None) -> None:
    from book_bot import start_catalog
    for msg in start_catalog(phone, name) or []:
        _send_text(phone, msg)


def _open_soc(phone: str, name: str | None) -> None:
    from book_bot import maybe_handle_soc
    for msg in maybe_handle_soc(phone, "sociology", name=name) or []:
        _send_text(phone, msg)


_PENDING_PAYMENT_STATES = ("awaiting_payment", "payment_review", "partially_paid")


def _pending_payment_reminder(phone: str, name: str | None) -> list[str] | None:
    """Reminder for a customer who already owes payment on a book order.

    start_catalog yields the reminder WITHOUT opening a fresh catalog when the
    order is in a payment state (see book_bot._start). Returns None when there is
    no such order. Never raises.
    """
    try:
        import db_cloud
        order = db_cloud.get_active_book_order(phone)
        if order and order.get("status") in _PENDING_PAYMENT_STATES:
            from book_bot import start_catalog
            return start_catalog(phone, name) or []
    except Exception as exc:
        logger.error("intent_router pending-payment check failed: %s", exc)
    return None


def route_front_door(phone: str, text: str, name: str | None = None) -> None:
    """Decide intent and perform the side effect. Sends everything internally."""
    # A customer who already owes payment on a book order is reminded first —
    # never dropped into a generic menu or a fresh catalog.
    reminder = _pending_payment_reminder(phone, name)
    if reminder:
        for msg in reminder:
            _send_text(phone, msg)
        return
    # Answer the ad BEFORE the intent, not instead of it. Someone who arrives
    # from "Print your Thesis for ₹0" and types "print" wants the order link AND
    # the offer they clicked; sending only one of the two loses half the
    # conversation. Fires once per person, so it cannot become noise.
    #
    # A tapped ice-breaker is a question the shop can answer itself, and it is
    # what most ad arrivals send first. Answer it here rather than letting it
    # fall to the menu (or to a human, nine hours later).
    #
    # It has to be decided BEFORE the welcome, because Meta delivers a tapped
    # ice-breaker as the very message that carries the `referral` object. The
    # old code answered it only `if not welcomed`, which is never true on the
    # one message an ice-breaker can arrive on: four of the six ad clicks
    # between 17-19 Sep 2026 opened this way ("What services do you offer?"
    # three times, "Rate send me" once) and not one of them was answered. The
    # feature had never fired in production. So hand the answer to the welcome
    # and let it send the two as one message.
    answer = icebreaker_reply(text)
    welcomed = _maybe_welcome_ad_arrival(phone, name, icebreaker=answer)
    if answer:
        if not welcomed:
            _send_text(phone, answer)
        # Their question has been answered, by name. Running the intent layers
        # on top would add a second message about something else -- "Can I book
        # an appointment?" classifies as `xtraa` and would post a book catalog.
        return

    intent = decide_intent(text)
    if intent in _LINK_MESSAGES:                # print / academic / notes
        # Compatible with the ad they clicked, so it is worth adding to the
        # welcome rather than instead of it.
        _send_text(phone, _LINK_MESSAGES[intent])
    elif welcomed:
        # A catalog is a DIFFERENT product, and following the ad welcome with
        # one is how a print-ad click got posted the Malayalam book list on
        # 19 Sep 2026. Standing book_bot down was only half the fix: the word
        # "book" in "Can I book an appointment?" classifies as `xtraa` here
        # too. The welcome answered them; leave it at that.
        return
    elif intent in ("xtraa", "malayalam"):
        _open_books(phone, name)               # Plan 1 interim: shared catalog
    elif intent == "sociology":
        _open_soc(phone, name)
    else:                                       # unknown / anything unhandled
        _unhandled(phone, text)


def _unhandled(phone: str, text: str) -> None:
    """Nothing understood the message. Menu, or a human if it was a question.

    api/index._handle_text has a handoff for exactly this, and it is
    unreachable from here: it is guarded by `customer_is_idle`, and an idle
    customer is precisely who gets routed into this function and returned on.
    Every ad arrival is idle, so the net has never caught one.

    The menu is the right answer to "hi". It is the wrong answer to a question,
    and repeating it is worse than saying nothing -- on 18 Sep 2026 a Hindi
    speaker off the ad got the same English list twice and then silence, with
    `needs_human` still false and no alert raised.

    Scoped to people who came from an ad, deliberately. They cost roughly
    Rs.164 each and there are a handful a day, so a staff_hold on one is cheap
    and losing one is not; the walk-in traffic that types something odd keeps
    the menu it has always had.
    """
    from routing.handoff import hand_off_to_human, should_handoff_text
    if should_handoff_text(text) and _came_from_an_ad(phone):
        hand_off_to_human(phone, text, "Front door couldn't answer an ad arrival")
        return
    _send_menu(phone)


def _came_from_an_ad(phone: str) -> bool:
    """Did this person arrive from a click-to-WhatsApp ad recently? Never raises."""
    try:
        from db_cloud import recent_ad_click
        return bool(recent_ad_click(phone))
    except Exception as exc:
        logger.warning("ad-arrival lookup failed for %s: %s", phone, exc)
        return False
