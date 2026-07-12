# Smart WhatsApp Front-Door Router — Implementation Plan (Plan 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the "every idle customer is dumped into the Xtraa book catalog" default with a smart front-door that detects what the customer actually wants and routes them to the correct flow or landing-page link — or a button menu when it isn't sure.

**Architecture:** A new pure-ish module `routing/intent.py` decides intent in four layers — (1) explicit tag from a landing-page deep-link or menu tap, (2) cheap deterministic keywords, (3) Claude Haiku classifier for free-form messages, (4) fall back to a WhatsApp button menu. A thin `route_front_door()` dispatcher performs the side effect (send a link, open an existing in-chat flow, or send the menu). Only ONE block in `api/index.py` changes — the idle-customer default — so rollback is a one-block revert. Everything upstream in the dispatch cascade (active book/soc sessions, notes, credits, help, tracking, website orders) is untouched.

**Tech Stack:** Python 3.11, pytest, Anthropic SDK (Claude Haiku 4.5, forced tool-use — mirrors `anu_parser.py`), existing `whatsapp_notify.send_list` / `_send` helpers.

**Scope note (Plan 1 interim routing):** Until Plan 2 lands, intents `xtraa` and `malayalam` both open the current books catalog (which still contains Malayalam). Plan 2 repoints `malayalam` to its own flow. This plan builds no new landing pages (Plan 3) — cold messages and the menu work without them.

---

## Reference facts (already verified in the codebase)

- The bad default lives in `api/index.py`, the idle-customer block. Currently:
  ```python
              from book_bot import start_catalog
              # ... comment ...
              for _reply in start_catalog(sender, name):
                  _send(sender, _reply)
              return
  ```
  Everything above it (active flows, notes, credits, help, tracking, website
  orders) already `return`s first — the router only fires for genuinely idle
  contacts.
- `whatsapp_notify._send(phone, message) -> bool` sends plain text.
- `whatsapp_notify.send_list(phone, body, button_text, rows, header=None, section_title="Options") -> bool`; `rows` = list of `{"id","title","description"?}`, max 10.
- `book_bot.start_catalog(phone, name=None) -> list[str]` opens the Xtraa catalog (sends the list internally, usually returns `[]`).
- `book_bot.maybe_handle_soc(phone, text, name=None) -> list[str] | None` opens the sociology flow when `text` triggers it (e.g. `"sociology"`).
- `book_bot.is_book_trigger(text) -> bool` and `book_bot.is_soc_trigger(text) -> bool` are existing deterministic keyword matchers.
- Classifier pattern to mirror: `anu_parser.parse_order_message` — forced tool-use, `MODEL = "claude-haiku-4-5"`, never raises, cost logged via `db_cloud.log_llm_cost`, reads `ANTHROPIC_API_KEY` from env.
- Tests: `pytest`; pure logic tested directly (see `tests/test_book_catalog.py`); heavy modules imported lazily inside functions to avoid cycles; `tests/conftest.py` already forces SQLite/in-memory and pre-imports real modules.

---

## File structure

| File | Responsibility |
|------|----------------|
| `routing/intent.py` | **New.** Intent constants, tag parser, keyword layer, Haiku classifier, `decide_intent`, menu builder, `route_front_door`. No webhook/HTTP knowledge. |
| `api/index.py` | **Modify (one block).** Replace the idle-customer `start_catalog` default with `route_front_door`. |
| `tests/test_intent_router.py` | **New.** Unit tests for every intent layer + the conversation simulation. |

---

## Task 1: Intent constants + tag parser (pure)

**Files:**
- Create: `routing/intent.py`
- Test: `tests/test_intent_router.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intent_router.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routing.intent import parse_intent_tag, INTENTS


class TestParseIntentTag:
    def test_hashtag_in_deeplink_message(self):
        assert parse_intent_tag("Hi, I want to print a file #print") == "print"

    def test_menu_row_id_exact(self):
        assert parse_intent_tag("intent_sociology") == "sociology"

    def test_soc_alias(self):
        assert parse_intent_tag("please send #soc") == "sociology"

    def test_case_insensitive(self):
        assert parse_intent_tag("ORDER XTRAA #XTRAA") == "xtraa"

    def test_no_tag_returns_none(self):
        assert parse_intent_tag("enikk oru book venam") is None

    def test_empty_returns_none(self):
        assert parse_intent_tag("") is None

    def test_all_intents_have_a_tag(self):
        for intent in INTENTS:
            assert parse_intent_tag(f"intent_{intent}") == intent
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_intent_router.py::TestParseIntentTag -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'routing.intent'` … `parse_intent_tag`.

- [ ] **Step 3: Write minimal implementation**

```python
# routing/intent.py
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
    "#print": "print",        "intent_print": "print",
    "#xtraa": "xtraa",        "intent_xtraa": "xtraa",
    "#malayalam": "malayalam","intent_malayalam": "malayalam",
    "#soc": "sociology",      "#sociology": "sociology", "intent_sociology": "sociology",
    "#academic": "academic",  "intent_academic": "academic",
    "#notes": "notes",        "intent_notes": "notes",
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_intent_router.py::TestParseIntentTag -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add routing/intent.py tests/test_intent_router.py
git commit -m "feat(routing): intent constants + deterministic tag parser"
```

---

## Task 2: Deterministic keyword layer

**Files:**
- Modify: `routing/intent.py`
- Test: `tests/test_intent_router.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_intent_router.py
from routing.intent import keyword_intent


class TestKeywordIntent:
    def test_print_words(self):
        for msg in ("i need a printout", "can you xerox this", "photocopy 10 pages"):
            assert keyword_intent(msg) == "print"

    def test_sociology_reuses_book_bot_trigger(self):
        assert keyword_intent("MA sociology sngu books") == "sociology"

    def test_academic_words(self):
        assert keyword_intent("need my project report binding") == "academic"

    def test_book_trigger_maps_to_xtraa_interim(self):
        # Plan 1 interim: any book intent opens the shared catalog.
        assert keyword_intent("malayalam book venam") == "xtraa"

    def test_plain_greeting_is_none(self):
        assert keyword_intent("hi") is None
        assert keyword_intent("hello") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_intent_router.py::TestKeywordIntent -v`
Expected: FAIL — cannot import `keyword_intent`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to routing/intent.py
_PRINT_WORDS = {
    "print", "printout", "print-out", "printing", "xerox", "photocopy",
    "photostat", "scan", "scanning",
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_intent_router.py::TestKeywordIntent -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add routing/intent.py tests/test_intent_router.py
git commit -m "feat(routing): deterministic keyword intent layer"
```

---

## Task 3: Haiku classifier + decide_intent chain

**Files:**
- Modify: `routing/intent.py`
- Test: `tests/test_intent_router.py`

- [ ] **Step 1: Write the failing test** (classifier is injected — no network in unit tests)

```python
# append to tests/test_intent_router.py
from routing.intent import decide_intent, CONFIDENCE_THRESHOLD


class TestDecideIntent:
    def test_tag_wins_first(self):
        called = {"n": 0}
        def fake(_): called["n"] += 1; return ("print", 0.99)
        assert decide_intent("open #sociology please", classifier=fake) == "sociology"
        assert called["n"] == 0  # LLM never consulted when a tag matches

    def test_keyword_before_llm(self):
        def fake(_): raise AssertionError("LLM should not be called")
        assert decide_intent("need a printout", classifier=fake) == "print"

    def test_llm_used_for_freeform_when_confident(self):
        def fake(_): return ("malayalam", 0.9)
        assert decide_intent("aksharamrutham kittumo", classifier=fake) == "malayalam"

    def test_low_confidence_falls_to_unknown(self):
        def fake(_): return ("academic", CONFIDENCE_THRESHOLD - 0.1)
        assert decide_intent("hmm something", classifier=fake) == "unknown"

    def test_llm_unknown_falls_to_unknown(self):
        def fake(_): return ("unknown", 0.0)
        assert decide_intent("blah blah", classifier=fake) == "unknown"

    def test_llm_bad_intent_ignored(self):
        def fake(_): return ("weather", 0.99)  # not a real intent
        assert decide_intent("random", classifier=fake) == "unknown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_intent_router.py::TestDecideIntent -v`
Expected: FAIL — cannot import `decide_intent` / `CONFIDENCE_THRESHOLD`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to routing/intent.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_intent_router.py::TestDecideIntent -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add routing/intent.py tests/test_intent_router.py
git commit -m "feat(routing): Haiku intent classifier + layered decide_intent"
```

---

## Task 4: Menu builder + route_front_door dispatcher

**Files:**
- Modify: `routing/intent.py`
- Test: `tests/test_intent_router.py`

- [ ] **Step 1: Write the failing test** (patch the send/flow seams; assert the right action)

```python
# append to tests/test_intent_router.py
import routing.intent as ir


class TestRouteFrontDoor:
    def setup_method(self):
        self.sent = []       # (phone, text) plain sends
        self.menus = []      # (phone, rows)
        self.opened = []     # flow name

    def _patch(self, monkeypatch, intent):
        monkeypatch.setattr(ir, "decide_intent", lambda text, name=None: intent)
        monkeypatch.setattr(ir, "_send_text", lambda phone, msg: self.sent.append((phone, msg)))
        monkeypatch.setattr(ir, "_send_menu", lambda phone: self.menus.append(phone))
        monkeypatch.setattr(ir, "_open_books", lambda phone, name: self.opened.append("books"))
        monkeypatch.setattr(ir, "_open_soc", lambda phone, name: self.opened.append("soc"))

    def test_print_sends_order_link(self, monkeypatch):
        self._patch(monkeypatch, "print")
        ir.route_front_door("91999", "whatever", "Ann")
        assert any("printosky.com/order" in m for _, m in self.sent)
        assert not self.opened and not self.menus

    def test_academic_sends_academic_link(self, monkeypatch):
        self._patch(monkeypatch, "academic")
        ir.route_front_door("91999", "x", None)
        assert any("printosky.com/academic" in m for _, m in self.sent)

    def test_xtraa_opens_books(self, monkeypatch):
        self._patch(monkeypatch, "xtraa")
        ir.route_front_door("91999", "x", None)
        assert self.opened == ["books"]

    def test_malayalam_opens_books_interim(self, monkeypatch):
        self._patch(monkeypatch, "malayalam")
        ir.route_front_door("91999", "x", None)
        assert self.opened == ["books"]

    def test_sociology_opens_soc(self, monkeypatch):
        self._patch(monkeypatch, "sociology")
        ir.route_front_door("91999", "x", None)
        assert self.opened == ["soc"]

    def test_unknown_sends_menu(self, monkeypatch):
        self._patch(monkeypatch, "unknown")
        ir.route_front_door("91999", "???", None)
        assert self.menus == ["91999"]

    def test_menu_rows_cover_every_intent(self):
        ids = {r["id"] for r in ir.build_menu_rows()}
        for intent in ("print", "xtraa", "malayalam", "sociology", "academic"):
            assert f"intent_{intent}" in ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_intent_router.py::TestRouteFrontDoor -v`
Expected: FAIL — cannot import `route_front_door` / `build_menu_rows`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to routing/intent.py

_ORDER_LINK = "https://printosky.com/order"
_ACADEMIC_LINK = "https://printosky.com/academic"

_LINK_MESSAGES = {
    "print": (
        "🖨️ *Print a file*\n"
        "Upload your file, pick paper / colour / copies and pay online here:\n"
        f"{_ORDER_LINK}\n\n"
        "— Oxygen Students Paradise, Thriprayar"
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
    """Rows for the tap-to-choose fallback menu (row id → parse_intent_tag)."""
    return [
        {"id": "intent_print",     "title": "🖨️ Print a file",   "description": "Documents, photos, PDFs"},
        {"id": "intent_xtraa",     "title": "📘 Xtraa books",     "description": "English & Hindi learning books"},
        {"id": "intent_malayalam", "title": "📗 Malayalam book",  "description": "Aksharamrutham"},
        {"id": "intent_sociology", "title": "📕 Sociology books", "description": "MA Sociology (SNGU)"},
        {"id": "intent_academic",  "title": "🎓 Academic project","description": "Project report & binding"},
    ]


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
                          "• Print a file: " + _ORDER_LINK + "\n"
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


def route_front_door(phone: str, text: str, name: str | None = None) -> None:
    """Decide intent and perform the side effect. Sends everything internally."""
    intent = decide_intent(text, name)
    if intent in _LINK_MESSAGES:
        _send_text(phone, _LINK_MESSAGES[intent])
    elif intent in ("xtraa", "malayalam"):
        _open_books(phone, name)               # Plan 1 interim: shared catalog
    elif intent == "sociology":
        _open_soc(phone, name)
    else:                                       # unknown / anything unhandled
        _send_menu(phone)
```

> **Note on `decide_intent(text, name)`:** the dispatcher calls `decide_intent(text, name)` but `decide_intent`'s second positional arg is `classifier`. Fix the call to keep the signatures honest — change the dispatcher line to `intent = decide_intent(text)` (name is not used for the decision). Update the `_patch` test's monkeypatch to `lambda text: intent` accordingly. (Do this now, before Step 4.)

- [ ] **Step 3b: Correct the decide_intent call**

In `route_front_door`, change:
```python
    intent = decide_intent(text, name)
```
to:
```python
    intent = decide_intent(text)
```
And in the test's `_patch`, change:
```python
        monkeypatch.setattr(ir, "decide_intent", lambda text, name=None: intent)
```
to:
```python
        monkeypatch.setattr(ir, "decide_intent", lambda text: intent)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_intent_router.py::TestRouteFrontDoor -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add routing/intent.py tests/test_intent_router.py
git commit -m "feat(routing): menu builder + route_front_door dispatcher"
```

---

## Task 5: Wire the router into the webhook (the one-block change)

**Files:**
- Modify: `api/index.py` (idle-customer default block only)

- [ ] **Step 1: Locate the exact block**

Run: `grep -n "from book_bot import start_catalog" api/index.py`
Expected: one hit inside the idle-customer block (`for _reply in start_catalog(sender, name):` follows a few lines below).

- [ ] **Step 2: Replace only that block**

Replace:
```python
            from book_bot import start_catalog
            # For a fresh/collecting cart start_catalog opens the catalog itself
            # (sends internally, returns []). For a customer who already has an
            # order awaiting payment it does NOT wipe it — it returns a "send your
            # payment / reply NEW" message instead. Forward whatever it returns so
            # that prompt (and the PAY-nudge reply path) isn't dropped.
            for _reply in start_catalog(sender, name):
                _send(sender, _reply)
            return
```
with:
```python
            # Smart front-door: classify what the customer actually wants and
            # route to the right flow/link instead of dumping everyone into the
            # Xtraa catalog. route_front_door sends everything internally.
            from routing.intent import route_front_door
            route_front_door(sender, text, name)
            return
```

> Leave the preceding abandoned-session `clear_session` block intact — a stale
> session is still reset before routing, so `_open_books` starts clean.

- [ ] **Step 3: Sanity-import check**

Run: `python -c "import ast,sys; ast.parse(open('api/index.py',encoding='utf-8').read()); print('api/index.py parses OK')"`
Expected: `api/index.py parses OK`

- [ ] **Step 4: Full suite must stay green**

Run: `pytest -q`
Expected: all pass (baseline is 64 passing; nothing above the idle block changed).

- [ ] **Step 5: Commit**

```bash
git add api/index.py
git commit -m "feat(webhook): route idle customers via smart front-door, not blind Xtraa default"
```

---

## Task 6: Conversation-simulation test (no wrong replies)

**Files:**
- Modify: `tests/test_intent_router.py`

This is the "prove it routes real messages correctly" test the owner asked for.
The LLM is mocked with a scripted map so the test is deterministic and free; it
exercises the FULL `decide_intent` chain (tag + keyword + LLM fallback).

- [ ] **Step 1: Write the test**

```python
# append to tests/test_intent_router.py
import pytest
from routing.intent import decide_intent


# (message, expected_intent). Free-form rows whose intent is not caught by tag or
# keyword are resolved by the mocked classifier below.
_SIM_CASES = [
    # deterministic — tags / keywords
    ("Hi, I want to print a file #print", "print"),
    ("need a printout of 3 pages", "print"),
    ("can you xerox this", "print"),
    ("MA sociology sngu book", "sociology"),
    ("intent_academic", "academic"),
    ("project report binding venam", "academic"),
    ("malayalam book venam", "xtraa"),        # interim: books → xtraa
    ("easy english book", "xtraa"),
    ("upload notes", "notes"),
    # free-form — resolved by the (mocked) classifier
    ("enikk oru file print cheyyanam", "print"),
    ("aksharamrutham kittumo", "malayalam"),
    ("sociology theory pusthakam undo", "sociology"),
    ("do you sell hindi books", "xtraa"),
    # unclear → menu
    ("hi", "unknown"),
    ("hello there", "unknown"),
    ("??", "unknown"),
]

# What the mocked Haiku would return for the free-form rows (keyword/tag miss).
_MOCK_LLM = {
    "enikk oru file print cheyyanam": ("print", 0.95),
    "aksharamrutham kittumo": ("malayalam", 0.9),
    "sociology theory pusthakam undo": ("sociology", 0.88),
    "do you sell hindi books": ("xtraa", 0.9),
}


@pytest.mark.parametrize("msg,expected", _SIM_CASES)
def test_conversation_simulation(msg, expected):
    def fake_classifier(text):
        return _MOCK_LLM.get(text, ("unknown", 0.0))
    assert decide_intent(msg, classifier=fake_classifier) == expected
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_intent_router.py::test_conversation_simulation -v`
Expected: PASS (16 parametrized cases).

- [ ] **Step 3: Run the whole router file**

Run: `pytest tests/test_intent_router.py -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_intent_router.py
git commit -m "test(routing): conversation simulation for front-door routing"
```

---

## Task 7: Live dry-run gate (manual, before go-live)

**Files:** none (verification only).

- [ ] **Step 1: Full suite green**

Run: `pytest -q`
Expected: all pass (baseline + new router tests).

- [ ] **Step 2: Real-LLM smoke check** (needs `ANTHROPIC_API_KEY`; costs a few paise)

Run:
```bash
python -c "from routing.intent import classify_intent; print(classify_intent('enikk oru file print cheyyanam')); print(classify_intent('sociology sngu book venam')); print(classify_intent('good morning'))"
```
Expected: roughly `('print', >0.6)`, `('sociology', >0.6)`, `('unknown', …)`.
If an intent is wrong or under-confident, tighten `_CLASSIFY_PROMPT` / enum
descriptions and re-run — do NOT lower `CONFIDENCE_THRESHOLD` to paper over it.

- [ ] **Step 3: Live WhatsApp dry-run on a test number / owner's phone**

From a phone that is NOT mid-flow, send each of: `hi`, `I want to print`,
`malayalam book`, `sociology sngu`, `random gibberish`. Confirm:
- `print` → order link, `sociology` → soc flow, book → catalog, gibberish → menu.
- No customer ever receives an off-topic reply.

- [ ] **Step 4: Tag owner for go/no-go**

Only after Steps 1–3 pass cleanly, deploy. Rollback = revert the Task 5 commit.

---

## Self-review notes

- **Spec coverage:** §3.1–3.4 (router, intents, routing table, `intent.py`) → Tasks 1–5. §6 testing (unit + simulation + dry-run) → Tasks 1–4, 6, 7. §4 landing pages and §5 catalog split are **out of scope by design** → Plans 3 and 2.
- **Interim behaviour is explicit:** `malayalam` → shared catalog until Plan 2 repoints it (documented in Task 2/4 and the simulation).
- **Type consistency:** `decide_intent(text, classifier=None)` — the dispatcher calls it with one arg (Step 3b corrects an initial two-arg slip). `classify_intent → (str, float)`. `INTENTS` excludes `"unknown"`; `"unknown"` is the sole menu trigger everywhere.
- **No placeholders:** every step has runnable code/commands and expected output.

---

## Roadmap (context, not part of this plan)

- **Plan 2 — Catalog split & Malayalam flow:** remove Malayalam from Xtraa (`BOOK_KEYS`, `_SELECT_IDS`, `_send_select_list`, `_parse_choice`, Anu `ANU_TEMPLATE`); retire `SET_PRICE` and set logic in `compute_totals`; add a dedicated Malayalam single-book flow (Divya commission, already coded); repoint `keyword_intent`/`route_front_door` `malayalam` to the new flow; catalog + flow tests.
- **Plan 3 — Landing pages:** `website/sociology.html` (+ `/sociology` redirect, CTA `#soc`), `website/malayalam.html` (Printosky + Divya co-branded, CTA `#malayalam`), rework `website/books.html` to English+Hindi (CTA `#xtraa`), add tags to existing CTAs.
