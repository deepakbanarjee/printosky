# Book-Buyer Capture — Auto-Answer + Typed Orders

**Date:** 2026-07-12
**Status:** Approved (design) — pending implementation plan
**Owner:** deepakbanarjee

---

## 1. Problem

Analysis of 20 non-converting WhatsApp chats (last 60 days) showed the expensive
losses were **book buyers who typed instead of tapped**, and **price/payment
questions the bot never answered**. Concrete lost leads:

- `9446089581`: "Aksharamrutham – 1 copy … Payment method? GPay? Which number? How much?" — a ready buyer, no order ever created.
- `8281673238`: "I want Malayalam & Hindi books. Can I GPay to …?"
- `9946784164`: "I wanted a Malayalam book."
- `9497173834`: "Aksharamritham"
- `8281755228`: "Bulk print price??" → "Agent" (routing loss — handled by the front-door work, not this spec).

Root causes this spec fixes:
1. The book flow only accepts **button taps** — free-typed orders create no order row.
2. **Price / delivery questions have no auto-answer** — payment is a QR shown only
   at the pay step (`book_bot._send_qr`); a customer asking "how much / which
   number" up front gets nothing.

## 2. Decisions (locked with owner)

| # | Decision |
|---|----------|
| D1 | **FAQ up front = price + delivery only.** Payment details are revealed only after an order is placed. |
| D2 | **Delivery quote = "3–5 days by courier."** |
| D3 | **Typed orders confirm first** (show item + price, wait for YES) before collecting address. |
| D4 | **Parser = deterministic-first + Haiku fallback** (`anu_parser`) only for messy phrasing. |
| D5 | Messages are **bilingual Malayalam + English**, matching the existing bot. |
| D6 | After a confirmed typed order, reuse the **existing name → address → phone → summary → payment** steps. |
| D7 | **Payment step adds a UPI-number fallback:** "Can't scan? Pay UPI to **9072034907** (GPay/PhonePe)" alongside the QR. Applies to every order. |

## 3. Architecture

New logic lives at the book-flow entry (`book_bot.maybe_handle_book`), which runs
**before** the front-door router and already receives the message text. When the
customer is **not** mid-flow, incoming book-ish messages are triaged:

1. **Names a book** (title or malayalam/hindi/english, ± a number) →
   **typed-order capture** → confirm → address.
2. **Pure price/delivery/payment question** (no book named) → **FAQ auto-answer**.
3. **Vague book trigger** ("books") → open the tap catalog (unchanged).
4. **Nothing book-ish** → return None → front-door handles it (unchanged).

Because typed orders confirm first, the confirmation message itself shows the
price, so "how much for aksharamrutham" is answered by the confirm step; the
standalone FAQ covers only questions with no book named.

## 4. Component 1 — FAQ auto-answer (price + delivery)

- **Trigger:** `book_catalog.is_book_faq(text)` — deterministic keyword match:
  `price, cost, rate, how much, ethra (എത്ര), delivery, courier, when, days,
  gpay, upi, payment, pay` and Malayalam equivalents (വില, എത്ര, കൊറിയർ). Only
  fires when **no** orderable book is parsed from the same message.
- **Reply:** built from `book_catalog.BOOKS` so it survives the Plan 2 catalog
  split. Bilingual. Lists each book + price, "+ courier from ₹75", delivery
  "3–5 days by courier", and payment held to post-order ("UPI sent once you
  place your order"). Ends with a CTA: reply with book + quantity, or tap 👇 →
  then opens the catalog list.

## 5. Component 2 — Typed-order capture + confirm

- `book_catalog.parse_customer_order(text) -> dict[str,int] | None`
  - Deterministic: map known titles/keywords → book keys
    (`aksharamrutham`/`അക്ഷരാമൃതം` → malayalam, `vidyamrut` → hindi,
    `easy english`/`english` → english, `malayalam`/`hindi` language words when
    clearly a book request), plus per-book quantity (default 1).
  - Returns the `{key: qty}` cart, or `None` when no book is clearly named.
- **Haiku fallback:** when the message looks book-ish but deterministic parsing
  yields nothing, reuse `anu_parser`-style extraction (Claude Haiku, forced
  tool-use, never raises) to recover book + qty. Sender phone is already known,
  so only book/qty need extracting.
- **Confirm step `book_confirm_parsed`:**
  - Send: "You want **<items>** = ₹<books_total> + courier. Reply **YES** to
    continue, or tap 👇 to change." (bilingual)
  - **YES / affirm** → create the order with the parsed items, set step to the
    existing **name** step, send the name prompt → existing flow continues to
    address → phone → summary → payment.
  - **NO / negate / list-tap** → open the catalog to pick manually.
  - Unrecognised reply → re-send the confirm prompt once, else fall back to catalog.

## 6. Payment step change (D7)

`book_bot._payment_caption` gains a UPI-number fallback line alongside the QR:
"Can't scan? Pay UPI to **9072034907** (GPay / PhonePe), then send the
screenshot here." Applies to all orders, not only typed ones.

## 7. Data flow

- No new tables. Typed orders use the existing `book_orders` row + `bot_sessions`
  step, created at confirm-YES exactly like a tapped order.
- FAQ sends are logged via the existing outbound log path.

## 8. Testing

1. `parse_customer_order` — titles, Manglish, single/multi-book, explicit qty,
   default qty, no-book (None). Unit tests (`tests/test_book_catalog.py` style).
2. `is_book_faq` — price/delivery/payment words (EN + Malayalam) true; plain
   greeting / book-name-only false.
3. FAQ reply text — contains each catalog price and "3–5 days"; prices read from
   `BOOKS` (not hard-coded).
4. `_handle_parsed_confirm` — YES → order created + name step; NO → catalog;
   junk → re-prompt.
5. `_payment_caption` — contains `9072034907`.
6. Integration via the `_handle_text` harness: "Aksharamrutham 1 copy" → confirm
   → "yes" → name prompt (no order dropped).

## 9. Scope / non-goals

- Ships independently of Plan 1 (front-door) and Plan 2 (catalog split). FAQ
  prices come from the catalog, so the Plan 2 split won't break the text.
- **No change** to commission, courier math, Razorpay, dispatch, or the
  front-door router.
- Not the full v2 conversation layer — this is the targeted, deterministic-first
  slice that plugs the measured leak.
