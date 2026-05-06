# WhatsApp Cloud API Templates

Pre-approved Meta WhatsApp Business templates that Printosky uses for **business-initiated** outbound messages — i.e. messages sent more than 24 hours after the customer's last inbound message. Within the 24h re-engagement window, free-form text is fine and the existing `whatsapp_notify.send_pickup_ready` / `send_pickup_completed` functions work without templates.

Submit these in **Meta Business Manager → WABA → Message Templates → Create Template**. Approval is usually within 24 hours. Once approved, switch the senders to use the template path (TODO: not yet wired into `whatsapp_notify.py`).

## Why both text and template variants exist

- **Within 24h of the customer's last inbound message:** free-form text works. Cheaper, faster, no template needed. The current MVP pickup notifications usually fall in this window (file → quote → pay → print → ready, often within a few hours).
- **After 24h:** Meta requires a pre-approved template. This catches the case where a customer pays Friday evening and the job goes ready Monday morning, or where we want to send a delayed status update.

## Template 1 — `pickup_ready_v1`

| Field | Value |
|---|---|
| **Name** | `pickup_ready_v1` |
| **Category** | UTILITY |
| **Language** | English (`en`) |
| **Header type** | None |

**Body:**

```
🎉 Your job is ready for pickup!

🎫 Code: *{{1}}*

📍 Pickup at:
{{2}}
{{3}}

Please show this code at the counter.
🔗 Track: {{4}}

— Printosky 🖨️
```

**Body variable examples:**
- `{{1}}` — pickup code, e.g. `P-7K2N`
- `{{2}}` — store display label, e.g. `Pickup Point A`
- `{{3}}` — store address, e.g. `Oxygen Students Paradise, Thrissur`
- `{{4}}` — deep link, e.g. `https://printosky.com/track?code=P-7K2N`

**Submission API payload (curl):**

```bash
curl -X POST \
  "https://graph.facebook.com/v21.0/${WABA_ID}/message_templates" \
  -H "Authorization: Bearer ${META_SYSTEM_USER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "pickup_ready_v1",
    "language": "en",
    "category": "UTILITY",
    "components": [
      {
        "type": "BODY",
        "text": "🎉 Your job is ready for pickup!\n\n🎫 Code: *{{1}}*\n\n📍 Pickup at:\n{{2}}\n{{3}}\n\nPlease show this code at the counter.\n🔗 Track: {{4}}\n\n— Printosky 🖨️",
        "example": {
          "body_text": [["P-7K2N", "Pickup Point A", "Oxygen Students Paradise, Thrissur", "https://printosky.com/track?code=P-7K2N"]]
        }
      }
    ]
  }'
```

## Template 2 — `pickup_completed_v1`

| Field | Value |
|---|---|
| **Name** | `pickup_completed_v1` |
| **Category** | UTILITY |
| **Language** | English (`en`) |
| **Header type** | None |

**Body:**

```
✅ Picked up — thank you!

🎫 {{1}}

We hope your prints turned out great.

How was your experience? ⭐ {{2}}

— Printosky 🖨️
```

**Body variables:**
- `{{1}}` — pickup code, e.g. `P-7K2N`
- `{{2}}` — rating link, e.g. `https://printosky.com/rate/P-7K2N` (rating page is out of MVP scope; submit with a `https://printosky.com/r/{{1}}` placeholder for now)

**Submission API payload:**

```bash
curl -X POST \
  "https://graph.facebook.com/v21.0/${WABA_ID}/message_templates" \
  -H "Authorization: Bearer ${META_SYSTEM_USER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "pickup_completed_v1",
    "language": "en",
    "category": "UTILITY",
    "components": [
      {
        "type": "BODY",
        "text": "✅ Picked up — thank you!\n\n🎫 {{1}}\n\nWe hope your prints turned out great.\n\nHow was your experience? ⭐ {{2}}\n\n— Printosky 🖨️",
        "example": {
          "body_text": [["P-7K2N", "https://printosky.com/r/P-7K2N"]]
        }
      }
    ]
  }'
```

## Template 3 (future, Block 5) — `job_dispatch_to_store_v1`

This is for the WhatsApp dispatch bot that goes to **store owners**, not customers. Documented here for completeness but not needed for Block 4. The interactive ACCEPT / REJECT / QUERY buttons require an INTERACTIVE template type.

```
New Printosky job: *{{1}}*

📦 {{2}}
🕐 Due {{3}}
👤 {{4}}

📎 Download: {{5}}

Reply ACCEPT, REJECT, or QUERY.
```

Variables: `{{1}}` pickup code, `{{2}}` job spec summary, `{{3}}` due-by, `{{4}}` customer first name, `{{5}}` signed file URL.

## Operational notes

- WABA ID is in the Meta Business Manager URL when you open the WABA (`business.facebook.com/wa/manage/?waba_id=<...>`). Set `WABA_ID` in `.env` before running the curl.
- `META_SYSTEM_USER_TOKEN` already in `.env` for Cloud API sends.
- Once approved, templates appear in `GET /v21.0/{WABA_ID}/message_templates` with `status: APPROVED`.
- Send via Cloud API by switching from `messages.text` to `messages.template` — the existing `_send_meta` function will need a `template_name` + `parameters` variant.
- Template *name* is what the API uses; the *category* (UTILITY vs. MARKETING) determines pricing and whether opt-in is required.
- Pickup-ready and pickup-completed are both UTILITY (transactional). Marketing-style templates would be a separate category and have stricter delivery rules.
