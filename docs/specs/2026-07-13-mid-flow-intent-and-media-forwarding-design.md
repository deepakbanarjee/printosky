# Mid-flow intent understanding + never-skip media forwarding

**Date:** 2026-07-13
**Status:** Approved (brainstorming)
**Area:** WhatsApp book-order bot (`book_bot.py`, `api/index.py`, `admin.html`)

## Problem

Two failures, both surfaced by order `XTR-20260712-D7F2D165` (Ajeesh J U, 918129692035):

1. **The bot doesn't understand mid-flow.** While a customer is in the `book_pay`
   state, `_handle_pay` ([book_bot.py:1054](../../../book_bot.py)) only recognizes
   `qr`, `edit`, `cancel`, and a pasted payment confirmation. Anything else falls to
   a default "please send a screenshot" reply. The customer typed *"Need one more
   book" → "Easy English"* to add a second book; the bot looped the screenshot
   prompt and never understood. He paid ₹275 (Malayalam + courier) and then ₹200
   (English) anyway, but the order stayed as a single Malayalam book. There is no
   intent layer and no Haiku fallback in mid-flow states — the existing Haiku
   parser (`_llm_parse_books`) only runs in `book_select`.

2. **Payment screenshots get dropped.** A screenshot is only treated as a payment
   when the session step is exactly `book_pay`. If the step has drifted (e.g. the
   session went `needs_human`, or the image arrives a beat late), the image is
   stored but never routed to Anu. Ajeesh's screenshots also never recorded
   `conversation_log.media_url`, so they were invisible in the admin transcript.

## Goals

- Understand what the customer is trying to say before falling back to a canned
  reply: deterministic parse → Haiku → human. Only tag a human when both machine
  layers fail.
- Handle "add a book after paying" automatically: add it, recompute, charge only
  the delta.
- Never drop a payment screenshot from a customer who owes money; always forward
  it to Anu with the live balance.
- Make every customer-sent image visible in the admin transcript with a download
  link.

## Non-goals

- Rebuilding or reusing the front-door router (`routing/intent.py`
  `route_front_door`) for mid-flow — it is built for idle/no-order customers and
  returns whole menus. Mid-flow is stateful (order + cursor + balance).
- Per-image vision classification. Payment detection is context-based (does the
  customer owe money), not pixel-based.
- Any change to the happy-path book flow (`book_select → book_qty → … → book_pay`).
- A rich admin media gallery. Thumbnail + link inside the existing transcript UI
  is enough.

## Design

### Feature 1 — mid-flow intent resolver

**New function** in `book_bot.py`:

```
resolve_stuck_message(phone, text, order, step) -> list[str] | None
```

Called by a dead-end handler *before* it emits its default reply. Returns a reply
list when it understood and handled the message; returns `None` to let the caller
fall through to the existing default (so behavior is strictly additive — if the
resolver is unsure it does nothing).

First consumer: `_handle_pay` (the `book_pay` state). Once proven there, the same
call is added to the other stateful dead-ends (`book_qty`, `book_summary`).

**The ladder (cheap → expensive → human):**

1. **Deterministic.** `bc.parse_customer_order(text)` plus book-name detection
   (`is_book_trigger` / the existing trigger phrases). If it yields one or more
   book keys → **add-a-book path** (below).
2. **Haiku.** If step 1 is empty:
   - `_llm_parse_books(text)` for book extraction (reuses `anu_parser`,
     forced tool-use, never raises).
   - `routing.intent.classify_intent(text)` for intent (returns
     `(intent, confidence)`; existing `claude-haiku-4-5` classifier with cost
     logging and a confidence threshold).
   - Map the result:
     - books extracted → add-a-book path
     - `cancel` → cancel the order (existing cancel logic)
     - price/"amount"/"how much" question → answer with the order's live
       total + amount paid + balance
     - `agent` / explicit human request → **escalate**
     - `unknown` or below the confidence threshold → **escalate**
3. **Escalate.** `save_session(..., needs_human=True)` so the bot stops
   auto-replying, and forward the customer's message + a chat link to Anu
   (`VERIFIER_PHONE`) via the existing `whatsapp_notify` text send. **No customer
   reply** is sent (owner chose "notify Anu + hold bot", not the tell-customer
   variant). A human clears `needs_human` through the existing staff path.

**Add-a-book + delta path:**

- Merge the detected book(s) into `order["items"]`.
- Recompute with `bc.divya_order_terms(phone, new_items, "courier")`
  (`books_total`, `courier`, `grand_total`, `commission`, `pradeep_commission`).
- `update_book_order(code, items=new_items, books_total=…, courier=…,
  grand_total=…)`. Leave `amount_paid` untouched; commission fields are finalized
  at confirm time by `confirm_book_order`, unchanged.
- `balance = grand_total - amount_paid`.
- Reply, bilingual, e.g.:
  *"➕ Added Easy English (+₹200). New total ₹475 · paid ₹275 · balance ₹200.
  Pay the balance and send a screenshot. 🙏"*
- Stay in `book_pay`.
- **Guard:** if `order["status"] == "confirmed"` or `amount_paid >= grand_total`
  (order already closed/fully paid), do **not** silently re-charge — escalate to
  Anu with a note that the customer wants to add a book to a completed order.

### Feature 2 — never-skip media + admin visibility

**a) Record every inbound image.** In `api/index.py` media handling, always write
`conversation_log.media_url` for every inbound image, including payment
screenshots (today they skip it). Payment images store their `book-payments/…`
storage path; other images store their `incoming-files` path. No schema change
(`media_url` column already exists).

**b) Forward on balance, not on session step.** Replace the "forward only when
session step == `book_pay`" rule with: **if the customer has an order whose status
is `awaiting_payment`, `partially_paid`, or `payment_review`** (i.e. money is
owed), treat any inbound image as a payment → `add_book_payment(code, url)` +
`_forward_to_verifier(order, payment, content, mime)` so Anu gets the screenshot,
the live balance, and Full/Part/Not-received buttons. Images from a customer with
no open balance are stored + linked in admin but not pushed to Anu.

**c) Admin transcript rendering.** In `admin.html`'s transcript viewer, an image
message renders an **inline thumbnail + download link** built from `media_url`
(public `incoming-files` URL). Reuses the existing native-fetch transcript UI; no
new endpoint.

## Data flow

```
inbound image (api/index.py)
  ├─ store to Supabase Storage  (existing)
  ├─ conversation_log insert with media_url   ← NEW: always, incl. payments
  └─ customer owes money on an order?
        ├─ yes → add_book_payment + _forward_to_verifier(Anu)   ← generalized
        └─ no  → link only (visible in admin transcript)

inbound text in book_pay (book_bot._handle_pay)
  ├─ qr / edit / cancel / pasted-payment   (existing inline handling)
  └─ else → resolve_stuck_message()   ← NEW
        ├─ deterministic book parse → add-a-book + delta
        ├─ Haiku book/intent → add-a-book | cancel | price answer | escalate
        └─ escalate → needs_human + ping Anu, bot silent
     (returns None → old default "send a screenshot" reply)
```

## Error handling

- Every Haiku / forward / classify call remains non-raising; the webhook must
  never 500. Any exception inside the ladder collapses to "escalate to human".
- `resolve_stuck_message` returning `None` is always safe — the caller's existing
  default reply runs, so no regression if the resolver is unsure.
- Delta-add validates that detected keys are in `bc.BOOKS`; on any recompute
  failure, escalate instead of writing a bad total.
- Forwarding failures are logged and non-fatal (the image is still stored + logged).

## Testing

Unit tests with the existing `FakeDB` in `tests/test_book_bot.py`:

- `book_pay` + "Need one more book" then "Easy English" → English added, total
  recomputed to ₹475, delta reply mentions the balance; still in `book_pay`.
- `book_pay` + fully-paid/confirmed order + "add a book" → escalates (guard), no
  silent re-charge.
- `book_pay` + gibberish → escalates: `needs_human` set, Anu notified, no customer
  reply.
- `book_pay` + "how much do I owe" → replies with live total/paid/balance.
- Inbound image with an outstanding-balance order → `add_book_payment` +
  forward-to-verifier called.
- Inbound image with no open balance → stored + `media_url` logged, not forwarded.
- `conversation_log.media_url` populated for a payment image.

## Rollout

- Feature 1 wired into `_handle_pay` first; extended to `book_qty` / `book_summary`
  after it's proven.
- Both features are additive and guarded, deployable together behind the normal
  webhook path (Vercel `api/index.py`). No migration.
