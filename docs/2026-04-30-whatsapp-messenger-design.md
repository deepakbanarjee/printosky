# WhatsApp Messenger Rebuild — Design Spec
**Date:** 2026-04-30  
**Branch:** sprint/session-9  
**Status:** Approved for implementation

---

## 1. Context & Problem

The admin chat UI (`admin.html` Conversations tab) shows all WhatsApp messages as `[file: filename]` text — unusable for images, voice notes, and documents. Media files are already uploaded to Supabase Storage (`incoming-files` bucket) during the Meta webhook handler, but **the URL is discarded** before being saved to `conversation_log`. The fix spans the full stack: DB schema, backend API, and frontend UI.

### Key discoveries that shaped this design
- `whatsapp_capture/index.js` is **fully retired** — commented out in `START_PRINTOSKY.bat`. All WhatsApp traffic flows through Meta Cloud API → `api/index.py` on Vercel.
- `watcher.py` still calls `localhost:3004` to send PDF invoices — **silently broken** since whatsapp_capture isn't running. Must fix.
- The `incoming-files` Supabase Storage bucket is **public** (`get_public_url()`). All customer documents (IDs, thesis drafts, personal photos) have permanent public URLs. Must change to signed URLs.
- `conversation_log` has `anon: SELECT` RLS — all customer conversations readable by anyone with the Supabase URL. Must remove.

---

## 2. Goals

1. **Media display** — inline images, audio player (voice notes), PDF download button, file type icons in chat bubbles
2. **Send files to customers** — staff can attach and send any file type from the chat UI
3. **Privacy** — private Supabase Storage with 1-hour signed URLs; RLS tightened so no direct anon DB access
4. **Contact names** — show customer name (WhatsApp pushname → jobs table → phone fallback)
5. **Auto-refresh** — 30s polling, paused when tab is hidden
6. **Notifications** — browser tab title badge + Web Notifications API
7. **Unread tracking** — synced across devices via Supabase

### Out of scope (this release)
- Quoted/reply-to messages
- Emoji picker
- Clipboard paste for images
- Video playback (download link only)
- Message search

---

## 3. Architecture

```
[WhatsApp customer]
      │  sends message
      ▼
[Meta Cloud API]
      │  webhook POST
      ▼
[api/index.py on Vercel]  ← _handle_webhook()
  1. Download media from Meta CDN (server-to-server)
  2. Lossless compress images (PNG only, skip JPEG/audio/video)
  3. Upload to Supabase Storage (private bucket)
  4. log_message(..., media_url=signed_url)  → conversation_log
  5. upsert_contact(phone, pushname)         → whatsapp_contacts
      │
      ▼
[Supabase]
  • conversation_log  (+ media_url column)
  • whatsapp_contacts (phone, name, last_seen_at)
  • Storage: incoming-files bucket (PRIVATE, signed URLs)

[website/admin.html — Conversations tab]
  • Polls GET /admin/conversations (inbox) every 30s
  • Polls GET /admin/thread?phone=X every 30s (active contact)
  • Renders: inline images, <audio> player, download buttons
  • Send text: POST /admin/send (existing)
  • Send file: browser → GET /admin/upload-token → upload to Supabase → POST /admin/send-file
  • Notifications: tab title badge + Web Notifications API
  • Unread: PATCH /admin/contacts/seen on contact open
```

---

## 4. Database Changes

### Migration: `api/migrations/SCHEMA_v16_chat.sql`

```sql
-- 1. Add media_url to conversation_log
ALTER TABLE conversation_log ADD COLUMN IF NOT EXISTS media_url TEXT;

-- 2. Contact name + unread tracking
CREATE TABLE IF NOT EXISTS whatsapp_contacts (
    phone        TEXT PRIMARY KEY,
    name         TEXT,
    last_seen_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE whatsapp_contacts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_all_contacts"
    ON whatsapp_contacts FOR ALL
    USING (true) WITH CHECK (true);
-- No anon access — contacts only read via backend proxy

-- 3. Tighten conversation_log RLS
-- Drop the open anon read policy
DROP POLICY IF EXISTS "anon_read_conversation_log" ON conversation_log;
-- No replacement — all reads go through /admin/conversations backend endpoint
```

### Supabase Storage
- Change `incoming-files` bucket from **public → private** in Supabase dashboard
- All URL generation switches from `get_public_url()` to `create_signed_url(path, expires_in=3600)`

---

## 5. Backend Changes

### 5a. `db_cloud.py`

**`log_message()` — add `media_url` parameter:**
```python
def log_message(phone, direction, body, message_type="text",
                filename=None, job_id=None, media_url=None) -> None:
    _client().table("conversation_log").insert({
        "phone": phone, "direction": direction,
        "message_type": message_type, "body": (body or "")[:2000],
        "filename": filename, "job_id": job_id, "media_url": media_url,
    }).execute()
```

**`upload_file()` — switch to private + signed URL:**
```python
def upload_file(filename, content, mime_type) -> str:
    _client().storage.from_(INCOMING_BUCKET).upload(filename, content,
        {"content-type": mime_type, "upsert": "false"})
    return get_signed_url(filename)

def get_signed_url(filename, expires_in=3600) -> str:
    resp = _client().storage.from_(INCOMING_BUCKET).create_signed_url(
        filename, expires_in)
    return resp["signedURL"]
```

**`upsert_contact()` — store name on first contact:**
```python
def upsert_contact(phone, name=None) -> None:
    data = {"phone": phone}
    if name:
        data["name"] = name
    _client().table("whatsapp_contacts").upsert(
        data, on_conflict="phone", ignore_duplicates=False).execute()

def mark_contact_seen(phone) -> None:
    _client().table("whatsapp_contacts").upsert(
        {"phone": phone, "last_seen_at": "now()"},
        on_conflict="phone").execute()
```

### 5b. `api/index.py` — webhook handler update

**Lossless image compression (new helper):**
```python
def _compress_lossless(data: bytes, mime: str) -> bytes:
    """Lossless PNG optimisation only. JPEG/audio/video/docs: pass through unchanged."""
    if mime != "image/png":
        return data  # never re-encode JPEG (already lossy) or non-images
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        compressed = buf.getvalue()
        return compressed if len(compressed) < len(data) else data
    except Exception:
        return data  # on any error, use original unchanged
```

**`_handle_webhook()` — save media_url:**
```python
media_data = _compress_lossless(raw_bytes, mime_type)
media_url   = db_cloud.upload_file(filename, media_data, mime_type)
db_cloud.log_message(phone, "inbound", caption,
                     message_type=media_type, filename=filename,
                     media_url=media_url)
db_cloud.upsert_contact(phone, name=pushname_from_contact)
```

### 5c. `api/index.py` — new endpoints

All endpoints authenticate via `admin_password` (same HMAC check as existing endpoints).

#### `GET /admin/conversations`
Returns inbox: one row per contact, last message preview, unread count.
```
Response: [{phone, name, last_message, last_message_type, unread_count, ts}]
```
Unread = messages with `created_at > whatsapp_contacts.last_seen_at` for that phone.

#### `GET /admin/thread?phone=X&limit=100`
Returns messages for one contact, newest last.
```
Response: [{id, direction, message_type, body, filename, media_url, created_at}]
```
Signs any `media_url` values fresh (new 1-hour expiry) before returning.

#### `PATCH /admin/contacts/seen` `{phone}`
Updates `whatsapp_contacts.last_seen_at = now()`. Called when staff opens a contact thread.

#### `POST /admin/upload-token` `{filename, mime_type}`
Returns a short-lived (5 min) Supabase Storage signed **upload** URL so the browser can upload directly to Supabase without routing through Vercel (avoids 4.5MB body limit).
```
Response: {upload_url, storage_path}
```

#### `POST /admin/send-file` `{phone, storage_path, caption, mime_type, filename}`
1. Downloads file from Supabase Storage (server-to-server)
2. Uploads to Meta media endpoint → gets `media_id`
3. Sends WhatsApp message with `media_id`
4. Calls `log_message(phone, "outbound", caption, message_type, filename, media_url=signed_url)`
```
Response: {ok: true}
```

### 5d. `whatsapp_notify.py` — add `send_file()`

```python
def send_file(phone: str, data: bytes, mime_type: str,
              filename: str, caption: str = "") -> bool:
    """Upload media to Meta then send as WhatsApp message."""
    # 1. Upload to Meta
    upload_resp = _meta_upload_media(data, mime_type, filename)
    media_id = upload_resp["id"]
    # 2. Send message
    msg_type = _mime_to_wa_type(mime_type)  # document/image/audio/video
    return _send_meta_media(phone, media_id, msg_type, caption, filename)
```

### 5e. `watcher.py` — fix broken port 3004 call

Replace dead `http://localhost:3004/send-document` call with:
```python
from whatsapp_notify import send_file as _wa_send_file
with open(invoice_path, "rb") as f:
    _wa_send_file(phone, f.read(), "application/pdf",
                  f"{inv_num}.pdf", caption=caption_text)
```

---

## 6. Media Storage Strategy

| Media type | Action | Stored permanently? |
|---|---|---|
| PNG image | Lossless PNG optimise; skip if compressed ≥ original | ✅ Yes |
| JPEG image | Store as-is (never re-encode lossy) | ✅ Yes |
| WebP / GIF | Store as-is | ✅ Yes |
| Voice note (audio/ogg, audio/mpeg) | Store as-is | ✅ Yes |
| Video (video/mp4) | Store as-is | ✅ Yes |
| PDF / Office docs | Store as-is | ✅ Yes |
| Stickers (image/webp) | Store as-is | ✅ Yes |

**Retention:** Supabase scheduled function (or Vercel cron) deletes files older than 90 days from `incoming-files`. Corresponding `media_url` values become stale — conversation record remains, media is gone. Acceptable for a print shop context.

**Signed URL refresh:** `/admin/thread` re-generates signed URLs on every fetch (1-hour expiry). Staff viewing an old thread always get a fresh, working URL.

---

## 7. Frontend Changes (`admin.html` — Conversations tab)

### 7a. Message bubble rendering

```
message_type  →  render
─────────────────────────────────────────────────────
text          →  plain text bubble
image/*       →  <img src="{media_url}" onclick="lightbox">
audio/*       →  🎤 Voice note  <audio controls src="{media_url}">
video/*       →  📹 Video  [Download] button (no inline player)
application/pdf  →  📄 {filename}  [Download] button
application/* →  📎 {filename}  [Download] button
[no media_url] →  italic grey "Media unavailable" (expired/old)
```

Images open in a full-screen lightbox (click to dismiss). Audio plays inline. Videos and documents get a download button that opens in a new tab.

### 7b. Reply bar

```
[📎]  [textarea — auto-expands, Enter sends, Shift+Enter newline]  [SEND]
```

- **📎 attach:** opens `<input type="file">` picker (any file type)
  - On file select → `POST /admin/upload-token` → upload directly to Supabase → `POST /admin/send-file`
  - Progress indicator shown in reply bar during upload
- **SEND button:** sends text via existing `POST /admin/send`
- Textarea auto-grows up to 5 lines, then scrolls

### 7c. Contact list (inbox)

- Shows: contact name (from `whatsapp_contacts.name`) or formatted phone fallback
- Last message preview (truncated 50 chars; shows "🎤 Voice note", "📄 filename" for media)
- Timestamp (HH:MM today, DD/MM for older)
- Unread count badge (red pill, hidden when 0)
- Contact name is upserted on first inbound message — appears immediately on next inbox refresh

### 7d. Auto-refresh

```javascript
// On tab visible: poll every 30s
// On tab hidden (document.hidden): pause polling
// On tab visible again: immediate poll + resume 30s interval
document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { pollNow(); startPolling(); }
    else stopPolling();
});
```

### 7e. Notifications

```javascript
// Tab title badge
document.title = unreadTotal > 0 ? `(${unreadTotal}) 💬 Printosky` : "Printosky Admin";

// Web Notifications (one-time permission request on page load)
if (Notification.permission === "default") Notification.requestPermission();

// Fire on new inbound message
new Notification(`New message from ${contactName}`, {
    body: preview,
    icon: "/favicon.ico",
    tag: phone  // replaces previous notification from same contact
});
```

### 7f. Authentication

Same `admin_password` prompt as existing admin.html (cached in `sessionStorage`). All API calls include `admin_password` as query param or header. No direct Supabase access from the browser.

---

## 8. Files Changed

| File | Type | What changes |
|---|---|---|
| `api/migrations/SCHEMA_v16_chat.sql` | New | `media_url` column, `whatsapp_contacts` table, drop anon RLS |
| `db_cloud.py` | Modified | `log_message` + `media_url`, signed URLs, `upsert_contact`, `mark_contact_seen` |
| `api/index.py` | Modified | Lossless compress helper, save `media_url` in webhook, 4 new endpoints |
| `whatsapp_notify.py` | Modified | Add `send_file()` using Meta media upload API |
| `watcher.py` | Modified | Fix dead port 3004 call → use `send_file()` |
| `website/admin.html` | Modified | Conversations tab: media bubbles, audio player, file send, notifications, unread badges |
| `requirements.txt` | Modified | Add `Pillow` for lossless PNG compression |

---

## 9. Security Summary

| Risk | Mitigation |
|---|---|
| Public customer media | Private Supabase Storage + 1-hour signed URLs |
| Open anon RLS on conversations | `anon_read` policy dropped; all reads via authenticated Vercel endpoint |
| Vercel file upload size limit | Browser uploads directly to Supabase via signed upload URL |
| Service role key exposure | Key stays in Vercel env vars; store PC unchanged |
| Sending files to wrong number | `phone` validated against known contacts in `whatsapp_contacts` before send |

---

## 10. Open Questions (resolved)

| # | Question | Decision |
|---|---|---|
| Tab vs page | Keep as tab in admin.html | Simpler for store staff |
| Contact names | WhatsApp pushname → jobs → phone | Upserted on every inbound |
| Storage | Private bucket, signed URLs, 90-day retention | All media types stored |
| Compression | PNG lossless only; JPEG/audio/video pass through | Quality never degraded |
| Real-time | 30s polling + visibility pause | No Realtime WebSocket complexity |
| Notifications | Tab title badge + Web Notifications API | Both implemented |
| Unread sync | `whatsapp_contacts.last_seen_at` in Supabase | Syncs across devices |
| File send path | Pre-signed upload → Vercel orchestrates Meta send | Bypasses 4.5MB limit |
| Video | Download link only (no inline player) | Scope reduction |
| watcher.py port 3004 | Replace with `whatsapp_notify.send_file()` | Fixes silent breakage |
