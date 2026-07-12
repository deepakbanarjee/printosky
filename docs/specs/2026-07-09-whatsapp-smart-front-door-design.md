# Smart WhatsApp Front-Door + Per-Operation Landing Pages

**Date:** 2026-07-09
**Status:** Approved (design) — pending implementation plan
**Owner:** deepakbanarjee
**Branch context:** work off `docs/uat` / feature branch TBD

---

## 1. Problem

Everything funnels through one WhatsApp number (`919495706405`), and the front-door
dispatch is wrong:

- `api/index.py` (the idle-customer block, ~lines 1096–1118) **defaults every
  unclaimed idle customer into the Xtraa book catalog.** The comment is explicit:
  *"whatever reaches here is a book prospect."*
- Consequence: a customer who wants to **print a file** is dumped into the book
  catalog. The in-chat print state machine (`whatsapp_bot.handle_message`) is
  effectively **unreachable** for new customers.
- `website/books.html` is **circular**: its only CTA is
  `wa.me/919495706405?text=Hi, I want to order Xtraa books`, funnelling people
  back into the same broken number.
- Only **sociology** routes correctly today, because it has trigger keywords
  (`sociology`, `sngu`, `soc`…). That keyword-funnel pattern is the model to
  generalise.

**Goal:** the WhatsApp number becomes a *smart receptionist* that understands
what each customer actually wants and routes them to the right flow or landing
page — never a blind/random reply.

## 2. Decisions (locked with owner)

| # | Decision |
|---|----------|
| D1 | **Hybrid model.** Keep the tested in-chat ordering flows; add a smart front-door that routes to the correct flow, and send a *link* only where web is genuinely better (file printing, academic). |
| D2 | **Catalog split.** Xtraa = **English + Hindi only**. Malayalam (Aksharamrutham) is a separate product with its own flow + page. The ₹549 **set-of-3 is retired**. |
| D3 | **Intent detection = Haiku classifier first → WhatsApp button menu on low-confidence/unknown.** (Reuse the Claude Haiku integration already used by `anu_parser.py`.) |
| D4 | **Commission unchanged.** Malayalam → Divya (₹50/book), English + Hindi → Pradeep (₹50/book). Already implemented via `commission` / `pradeep_commission` and `divya_ledger` / `pradeep_ledger`. **No money-logic changes.** |
| D5 | **Malayalam landing page = Printosky + Divya co-branded** (new page under printosky.com, carrying both brands; not the standalone drdivyam.com site). |
| D6 | Academic projects and Notes marketplace are included in the router/menu so no live operation is orphaned. |
| D7 | File printing → reply with the **printosky.com/order** link (not the old in-chat print machine). |

## 3. Architecture

### 3.1 Front-door router (replaces the blind-default block only)

Higher-priority handlers stay **untouched** — active book/soc session, notes,
credits, help/vendor/tracking. They already work and already `return` before the
default. We replace only the idle-customer default block in `api/index.py`.

Two arrival paths, one router:

1. **From a landing page** — the page CTA is a deep link
   `wa.me/919495706405?text=<message with intent tag>`. The router parses a known
   tag → routes deterministically. Free, instant, exact.
2. **Cold message** (no tag) — Haiku classifier reads the message and returns an
   intent. On `unknown` **or** low confidence → send a WhatsApp button menu.

### 3.2 Intent set

`print · xtraa · malayalam · sociology · academic · notes · unknown`

### 3.3 Routing table

| Intent | Action |
|--------|--------|
| `print` | Reply with **printosky.com/order** link (file upload → web is better) |
| `xtraa` | Launch in-chat Xtraa flow (English + Hindi) — `start_catalog` |
| `malayalam` | Launch in-chat Malayalam flow (Aksharamrutham) |
| `sociology` | Existing in-chat sociology flow (`maybe_handle_soc`) |
| `academic` | Reply with **printosky.com/academic** link |
| `notes` | Existing notes handler |
| `unknown` | WhatsApp button menu: Print a file · Xtraa books · Malayalam book · Sociology · Something else |

### 3.4 New module: `routing/intent.py`

- `parse_intent_tag(text) -> str | None` — deterministic; recognises the tags
  emitted by landing-page deep links. Pure, unit-testable.
- `classify_intent(text) -> (intent: str, confidence: float)` — Haiku call with
  forced tool-use (mirrors `anu_parser.py`), returns one intent + confidence.
  Cost logged via `db_cloud.log_llm_cost`.
- `build_intent_menu(...)` — constructs the WhatsApp interactive button/list menu
  payload for the `unknown` path.

Kept separate from `api/index.py` so the classifier and tag parser are testable
without the webhook.

## 4. Landing pages

| Page | URL | Status | Work |
|------|-----|--------|------|
| File printing | `/order` | Exists, works | None — becomes the link we send |
| Xtraa (Eng + Hindi) | `/books` | Exists but circular + sells 3 books | Rework: drop Malayalam, sell Eng+Hindi, CTA deep-link `#xtraa` |
| Malayalam (Aksharamrutham) | new `/malayalam` | Missing | Build Printosky + Divya co-branded showcase page, CTA deep-link `#malayalam` |
| Sociology | new `/sociology` | Missing | Build showcase page, CTA deep-link `#soc` |
| Academic | `/academic` | Exists | None — becomes the link we send |

Every page is a **funnel into the correct in-chat flow / link**, replacing the
current bounce-into-a-broken-number.

## 5. Catalog & commission changes

Files: `book_catalog.py`, `book_bot.py`.

- Xtraa catalog → English + Hindi only (`BOOKS` / `BOOK_KEYS`).
- **Retire** `SET_PRICE` (₹549) and all set-of-3 selection/pricing logic
  (`_ALL_TOKENS`, set discount branches).
- **Malayalam** → its own single-book flow (Aksharamrutham, ₹200), parallel to
  the sociology flow pattern.
- Commission: **no change.** `commission` (Divya/Malayalam) and
  `pradeep_commission` (Pradeep/Eng+Hindi) already compute per-book and settle via
  the existing ledgers. Divya self-order exception preserved.

## 6. Testing (must pass before go-live)

1. **Unit tests**
   - `parse_intent_tag` — every tag + garbage.
   - `classify_intent` — mocked Haiku over realistic inputs: Malayalam/English mix,
     typos, one-word ("print", "sociology"), ambiguous, gibberish.
   - Catalog after split — totals, courier, commission (Divya vs Pradeep) with
     Malayalam removed from Xtraa and the set retired.
   - Menu builder payload shape.
2. **Conversation simulation harness** — ~30 realistic first-messages asserting the
   correct route + reply. Explicitly proves "no wrong replies to customers."
3. **Live dry-run** on a test number / owner's phone before pointing the real
   number at the new router.
4. Existing `pytest` suite stays green (64 tests today).

## 7. Rollout & rollback

- Deploy router + landing pages. Flip landing-page CTAs to the new intent tags.
- **Rollback** = revert the single dispatch block in `api/index.py`
  (landing pages are additive and harmless if the router isn't live).

## 8. File-change summary

| File | Change |
|------|--------|
| `api/index.py` | Replace idle-customer default block with router (tag parse → classifier → menu). |
| `routing/intent.py` | **New.** Tag parser, Haiku classifier, menu builder. |
| `book_catalog.py` | Xtraa = Eng+Hindi; retire set-of-3; add Malayalam single-book. |
| `book_bot.py` | Xtraa flow Eng+Hindi; new Malayalam flow; intent trigger words. |
| `website/books.html` | Rework to Eng+Hindi, new CTA tag. |
| `website/malayalam.html` | **New.** Printosky + Divya co-branded. |
| `website/sociology.html` | **New.** |
| `website/_redirects` | Add `/malayalam`, `/sociology`. |
| `tests/…` | New: intent tests, catalog-split tests, conversation simulation. |

## 9. Non-goals (this build)

- **No self-learning / fine-tuning.** The classifier is one-shot inference. A
  multilingual *conversation* layer is a deliberate follow-on — see §10.
- No change to Razorpay, dispatch, courier, or the `routing/engine.py` geospatial
  job router.
- No change to how any individual book is priced or how commission is computed.
  (Retiring the ₹549 set removes a *bundle option* from the UI; per-book price and
  the Divya/Pradeep commission math are untouched.)

## 10. v2 (Deferred): Multilingual conversation layer

Not in this build. Recorded so the current work stays v2-ready and scope is
explicit.

### 10.1 What v2 is — and what it is NOT

- **IS:** an LLM (Haiku/Sonnet) that *converses* in the customer's language —
  English, Malayalam, or Manglish (Latin-script Malayalam) — understanding
  free-form intent and rephrasing the system's fixed facts into that language.
- **IS NOT:** self-learning / fine-tuning / feedback-trained models. The value
  the owner wants (warm, smart, multilingual chat) comes from *prompting a good
  base model*, not from training one. True self-learning is explicitly deferred
  until (a) there is real volume and (b) a specific, measured weakness base models
  cannot handle. Everything is already logged, so the option stays open at zero
  cost today.

### 10.2 Hard constraint (non-negotiable): LLM is the ears and mouth, the state machine is the brain for money

The deterministic flows remain the single source of truth for anything
transactional — **prices, order capture, payment links, order codes, delivery
promises, commission.** The LLM may:

- read and classify intent,
- converse and disambiguate in the customer's language,
- **rephrase fixed facts** the system provides into that language.

The LLM may **never invent** a price, discount, date, policy, link, or order
code. Money-bearing tokens (₹ amounts, URLs, `OSP-…` codes) are passed through
verbatim from the deterministic layer and must not be regenerated by the model.
This is what prevents money-hallucination — the single biggest risk of a chatty
bot in a billing business.

### 10.3 Language handling

- Detect the customer's language/script and reply in kind (English → English,
  Manglish → Manglish, Malayalam → Malayalam).
- Preserve respectful register in Malayalam (formality / elder-addressing).
- Fixed tokens (prices, links, codes) stay as-is regardless of reply language.

### 10.4 Risks to design against (before v2 ships)

- Money hallucination (mitigated by §10.2).
- Manglish spelling inconsistency / regional-term misreads.
- Register/tone misfires in Malayalam.
- Verification: cannot be unit-tested like a state machine — needs an **eval
  set + spot-checks**, not pass/fail tests.
- Per-message cost & latency (every turn → LLM call).
- Prompt-injection / abuse hardening.
- Data-privacy/consent if chats are ever used for training (a reason to keep
  self-learning out until explicitly justified).

### 10.5 Why the current build is already v2-ready

The §3.4 Haiku classifier is the "ears." v2 adds the "mouth" (multilingual reply
generation) on the same integration, still routing into the same deterministic
flows. No part of this build is thrown away when v2 begins.
