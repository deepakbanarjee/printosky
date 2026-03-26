"""
PRINTOSKY WEBHOOK RECEIVER
============================
Lightweight HTTP server that receives Razorpay payment webhooks.
Runs on localhost:3002, exposed via Cloudflare Tunnel to the internet.

When Razorpay confirms a payment:
  1. Verifies signature
  2. Updates job status to 'Paid' in SQLite (single job OR all jobs in a batch)
  3. Sends "Payment confirmed, printing now" WhatsApp to customer
  4. Saves customer profile (repeat-customer settings)
  5. Staff sees job turn green on dashboard

Setup:
  - Set WEBHOOK_SECRET in razorpay_integration.py
  - Set same secret in Razorpay Dashboard → Settings → Webhooks
  - Run cloudflared tunnel (see WEBHOOK_SETUP.md)
  - Set tunnel URL as webhook URL in Razorpay
"""

import sys
import os
import re
import json
import sqlite3
import logging
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# Ensure stdout handles Unicode/emoji on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

logger = logging.getLogger("webhook_receiver")

# ── Config ────────────────────────────────────────────────────────────────────
WEBHOOK_PORT = 3002

# ── Request handler ───────────────────────────────────────────────────────────
class WebhookHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        logger.debug(f"Webhook HTTP: {format % args}")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)

        # ── AiSensy incoming message webhook ──────────────────────────────────
        if self.path == "/webhook/aisensy":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            try:
                data = json.loads(body)
                threading.Thread(
                    target=process_aisensy_message,
                    args=(data, self.server.db_path),
                    daemon=True,
                ).start()
            except Exception as e:
                logger.error(f"AiSensy webhook parse error: {e}")
            return

        # ── Razorpay payment webhook ───────────────────────────────────────────
        if self.path != "/webhook/razorpay":
            self.send_response(404)
            self.end_headers()
            return

        sig = self.headers.get("X-Razorpay-Signature", "")

        # Verify signature
        from razorpay_integration import verify_webhook, parse_payment_webhook
        if not verify_webhook(body, sig):
            logger.warning("Webhook signature verification failed")
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Invalid signature")
            return

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

        # Process in background so we return 200 fast
        try:
            data = json.loads(body)
            threading.Thread(
                target=process_payment,
                args=(data, self.server.db_path),
                daemon=True,
            ).start()
        except Exception as e:
            logger.error(f"Webhook parse error: {e}")

    def do_GET(self):
        # Health check
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Printosky webhook receiver OK")


# ── AiSensy incoming message processor ───────────────────────────────────────
INCOMING_FOLDER = r"C:\Printosky\Jobs\Incoming"

# Supported file MIME types
FILE_MIME_TYPES = {
    "application/pdf":                                                 ".pdf",
    "application/msword":                                              ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-powerpoint":                                   ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-excel":                                        ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "image/jpeg":                                                      ".jpg",
    "image/png":                                                       ".png",
    "image/gif":                                                       ".gif",
    "image/webp":                                                      ".webp",
}


def _extract_aisensy_fields(data: dict):
    """
    Parse AiSensy webhook payload and return (sender_phone, msg_type, payload_inner).
    AiSensy wraps messages in:
      { "type": "message", "payload": { "type": "text|document|image|...",
        "payload": { ... }, "sender": { "phone": "91...", "name": "..." } } }
    """
    try:
        outer = data.get("payload", data)          # tolerate flat or nested
        msg_type     = outer.get("type", "")
        inner        = outer.get("payload", {})
        sender_info  = outer.get("sender", {})
        sender_phone = (
            sender_info.get("phone")
            or outer.get("source")
            or data.get("from")
            or ""
        )
        return sender_phone, msg_type, inner
    except Exception as e:
        logger.error(f"AiSensy field extract error: {e}")
        return "", "", {}


def process_aisensy_message(data: dict, db_path: str):
    """Route an incoming AiSensy webhook message to the right handler."""
    sender, msg_type, inner = _extract_aisensy_fields(data)

    if not sender:
        logger.warning(f"AiSensy webhook: could not extract sender. Raw: {json.dumps(data)[:300]}")
        return

    logger.info(f"AiSensy message from {sender}: type={msg_type}")

    # ── File / media message ──────────────────────────────────────────────────
    if msg_type in ("document", "image", "video", "audio"):
        _handle_aisensy_file(sender, msg_type, inner, db_path)
        return

    # ── Text message ─────────────────────────────────────────────────────────
    if msg_type == "text":
        text = inner.get("text") or inner.get("body") or ""
        _handle_aisensy_text(sender, text.strip(), db_path)
        return

    logger.debug(f"AiSensy: unhandled message type '{msg_type}' from {sender}")


def _handle_aisensy_file(sender: str, msg_type: str, inner: dict, db_path: str):
    """Download a file from AiSensy media URL and save to hot folder."""
    media_url = inner.get("url") or inner.get("link") or ""
    if not media_url:
        logger.warning(f"AiSensy file from {sender}: no URL in payload {inner}")
        return

    # Determine filename
    caption   = inner.get("caption") or inner.get("filename") or ""
    mime_type = inner.get("mimeType") or inner.get("mime_type") or ""
    ext       = FILE_MIME_TYPES.get(mime_type, "")

    if caption and "." in caption:
        base_name = re.sub(r'[^\w.\- ]', '_', caption).strip()
    else:
        ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"{sender}_{ts}{ext or '.bin'}"

    dest_path = os.path.join(INCOMING_FOLDER, base_name)

    # Avoid duplicate filenames
    if os.path.exists(dest_path):
        name, dot_ext = os.path.splitext(base_name)
        dest_path = os.path.join(INCOMING_FOLDER, f"{name}_{datetime.now().strftime('%H%M%S')}{dot_ext}")

    # Download
    try:
        r = requests.get(media_url, timeout=60)
        r.raise_for_status()
        os.makedirs(INCOMING_FOLDER, exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(r.content)
        logger.info(f"AiSensy file saved: {dest_path} ({len(r.content)} bytes)")
    except Exception as e:
        logger.error(f"AiSensy file download failed from {media_url}: {e}")
        return

    # Write .sender sidecar (watcher.py reads this to know customer phone)
    sender_path = dest_path + ".sender"
    try:
        with open(sender_path, "w") as f:
            f.write(sender)
        logger.info(f"Sender file written: {sender_path}")
    except Exception as e:
        logger.error(f"Failed to write .sender file: {e}")

    # watcher.py will pick up the file via Watchdog — no further action needed here


def _handle_aisensy_text(sender: str, text: str, db_path: str):
    """Forward a customer text reply to the bot handler."""
    if not text:
        return
    # Post to the bot handler running on port 3003 (same as index.js did)
    try:
        r = requests.post(
            "http://localhost:3003/bot",
            json={"phone": sender, "message": text},
            timeout=10,
        )
        if r.status_code == 200:
            logger.info(f"AiSensy text forwarded to bot: {sender} → '{text[:60]}'")
        else:
            logger.warning(f"Bot handler returned {r.status_code} for {sender}")
    except Exception as e:
        logger.warning(f"AiSensy text forward error: {e}")


# ── Payment processor ─────────────────────────────────────────────────────────
def process_payment(data: dict, db_path: str):
    """Handle confirmed payment — update DB, notify customer, notify staff."""
    from razorpay_integration import parse_payment_webhook
    from whatsapp_notify import send_payment_confirmed

    payment = parse_payment_webhook(data)
    if not payment:
        logger.debug(f"Webhook event ignored: {data.get('event')}")
        return

    ref_id = payment["job_id"]   # could be a job_id (OSP-...) or batch_id (BATCH-...)
    amount = payment["amount"]
    method = payment["method"]
    pay_id = payment["payment_id"]

    logger.info(f"Payment confirmed: {ref_id} ₹{amount} via {method} ({pay_id})")

    # ── Check if this is a batch payment ─────────────────────────────────────
    try:
        conn      = sqlite3.connect(db_path)
        batch_row = conn.execute(
            "SELECT batch_id, phone, job_ids FROM job_batches WHERE batch_id=?", (ref_id,)
        ).fetchone()
        conn.close()
    except Exception as e:
        logger.error(f"Batch lookup failed: {e}")
        batch_row = None

    if batch_row:
        _process_batch_payment(batch_row, amount, method, pay_id, db_path)
        return

    # ── Single job payment ────────────────────────────────────────────────────
    job_id = ref_id
    try:
        conn = sqlite3.connect(db_path)
        row  = conn.execute(
            "SELECT sender, filename FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        conn.execute("""
            UPDATE jobs
            SET status='Paid', amount_collected=?, payment_mode=?,
                razorpay_payment_id=?
            WHERE job_id=?
        """, (amount, method, pay_id, job_id))
        conn.commit()
        conn.close()
        logger.info(f"Job {job_id} status → Paid in DB")
    except Exception as e:
        logger.error(f"DB update failed for {job_id}: {e}")
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("ALTER TABLE jobs ADD COLUMN razorpay_payment_id TEXT")
            conn.execute("""
                UPDATE jobs SET status='Paid', amount_collected=?,
                payment_mode=?, razorpay_payment_id=? WHERE job_id=?
            """, (amount, method, pay_id, job_id))
            conn.commit()
            conn.close()
        except Exception as e2:
            logger.error(f"DB retry failed: {e2}")
        return

    if row and row[0]:
        sender   = row[0]
        send_payment_confirmed(sender, job_id, amount)
        logger.info(f"Customer notified: {sender}")
    else:
        logger.warning(f"No sender found for {job_id} — skipping customer WhatsApp")

    print(f"\n{'='*50}")
    print(f"  💚 PAYMENT RECEIVED — PRINT NOW")
    print(f"  Job: {job_id}")
    print(f"  Amount: ₹{amount:.2f} via {method}")
    print(f"  Payment ID: {pay_id}")
    print(f"  Type 'done {job_id}' when printing is complete")
    print(f"{'='*50}\n")


def _process_batch_payment(batch_row, amount: float, method: str, pay_id: str, db_path: str):
    """Mark all jobs in a batch as Paid, notify customer, save customer profile."""
    from whatsapp_notify import send_payment_confirmed
    from whatsapp_bot import save_customer_profile

    batch_id, phone, job_ids_str = batch_row
    job_ids = [j for j in (job_ids_str or "").split(",") if j.strip()]

    if not job_ids:
        logger.warning(f"Batch {batch_id} has no job_ids — skipping")
        return

    try:
        conn = sqlite3.connect(db_path)
        for jid in job_ids:
            conn.execute(
                "UPDATE jobs SET status='Paid', payment_mode=?, razorpay_payment_id=? WHERE job_id=?",
                (method, pay_id, jid)
            )
        conn.execute(
            "UPDATE job_batches SET status='paid' WHERE batch_id=?", (batch_id,)
        )
        conn.commit()

        # Save customer profile using last job's settings
        last_job = conn.execute(
            "SELECT size, colour, layout, copies, finishing, delivery FROM jobs WHERE job_id=?",
            (job_ids[-1],)
        ).fetchone()

        conn.close()
        logger.info(f"Batch {batch_id}: {len(job_ids)} job(s) marked Paid")
    except Exception as e:
        logger.error(f"Batch DB update failed for {batch_id}: {e}")
        return

    if last_job and last_job[0]:
        try:
            save_customer_profile(phone, {
                "size":     last_job[0],
                "colour":   last_job[1],
                "layout":   last_job[2],
                "copies":   last_job[3] or 1,
                "finishing": last_job[4] or "none",
                "delivery": last_job[5] or 0,
            }, db_path)
            logger.info(f"Customer profile saved for {phone}")
        except Exception as e:
            logger.warning(f"save_customer_profile failed: {e}")

    # Notify customer
    n = len(job_ids)
    send_payment_confirmed(phone, batch_id, amount)
    logger.info(f"Customer {phone} notified — batch {batch_id}")

    try:
        print(f"\n{'='*50}")
        print(f"  [PAID] BATCH PAYMENT RECEIVED — PRINT NOW")
        print(f"  Batch : {batch_id}  ({n} job(s))")
        print(f"  Amount: Rs.{amount:.2f} via {method}")
        print(f"  Pay ID: {pay_id}")
        for jid in job_ids:
            print(f"  Type 'done {jid}' when printing is complete")
        print(f"{'='*50}\n")
    except Exception:
        logger.info(f"[PAID] Batch {batch_id} — Rs.{amount:.2f} via {method} — {n} job(s)")


# ── Start server ──────────────────────────────────────────────────────────────
def start_webhook_server(db_path: str, port: int = WEBHOOK_PORT):
    """Start webhook receiver in a background daemon thread."""
    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
    server.db_path = db_path

    def run():
        logger.info(f"Razorpay webhook receiver on port {port}")
        server.serve_forever()

    t = threading.Thread(target=run, daemon=True, name="WebhookReceiver")
    t.start()
    logger.info(f"Webhook receiver started — :{port}/webhook/razorpay + :{port}/webhook/aisensy")
    return t
