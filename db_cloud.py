"""
PRINTOSKY CLOUD DB ADAPTER
===========================
Supabase backend for whatsapp_bot.py and webhook_receiver.py.
Activated when SUPABASE_URL env var is set (Vercel deployment).
The store PC continues to use the SQLite path (no SUPABASE_URL).

All functions mirror the SQLite function signatures exactly so
whatsapp_bot.py can swap backends transparently.
"""

import os
import logging
from datetime import datetime, timezone, timedelta

# Note: pickup_code and routing.engine are imported lazily inside
# update_job_paid (see below). Keeping them out of module-top means a
# missing module in deployment cannot kill every db_cloud importer.

logger = logging.getLogger("db_cloud")

# ── Supabase client (lazy singleton) ─────────────────────────────────────────

_sb = None


def _client():
    global _sb
    if _sb is None:
        from supabase import create_client
        url = os.environ["SUPABASE_URL"]
        key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
        _sb = create_client(url, key)
    return _sb


# ── bot_sessions ──────────────────────────────────────────────────────────────

def get_session(db_path: str, phone: str) -> dict:
    """Fetch bot session from Supabase (db_path is ignored in cloud mode)."""
    try:
        result = _client().table("bot_sessions").select("*").eq("phone", phone).execute()
        return result.data[0] if result.data else {}
    except Exception as e:
        logger.error(f"get_session error for {phone}: {e}")
        return {}


def save_session(db_path: str, phone: str, **kwargs) -> None:
    """Upsert bot session into Supabase, updating only the provided fields."""
    kwargs["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        # UPDATE existing row; if no rows matched, INSERT a new one.
        result = _client().table("bot_sessions").update(kwargs).eq("phone", phone).execute()
        if not result.data:
            kwargs["phone"] = phone
            _client().table("bot_sessions").insert(kwargs).execute()
    except Exception as e:
        logger.error(f"save_session error for {phone}: {e}")


def clear_session(db_path: str, phone: str) -> None:
    """Delete bot session from Supabase."""
    try:
        _client().table("bot_sessions").delete().eq("phone", phone).execute()
    except Exception as e:
        logger.error(f"clear_session error for {phone}: {e}")


# ── customer_profiles ─────────────────────────────────────────────────────────

def save_customer_profile(phone: str, settings: dict, db_path: str) -> None:
    """Upsert customer's last-used print settings."""
    row = {
        "phone":          phone,
        "last_size":      settings["size"],
        "last_colour":    settings["colour"],
        "last_layout":    settings["layout"],
        "last_copies":    settings["copies"],
        "last_finishing": settings["finishing"],
        "last_delivery":  int(settings["delivery"]),
        "updated_at":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        _client().table("customer_profiles").upsert(row, on_conflict="phone").execute()
    except Exception as e:
        logger.error(f"save_customer_profile error for {phone}: {e}")


# ── jobs ──────────────────────────────────────────────────────────────────────

def get_job(job_id: str) -> dict:
    """Fetch a single job row."""
    try:
        result = _client().table("jobs").select("*").eq("job_id", job_id).execute()
        return result.data[0] if result.data else {}
    except Exception as e:
        logger.error(f"get_job error for {job_id}: {e}")
        return {}


def get_job_filepath(job_id: str) -> str | None:
    """Return file_url (cloud) or filepath (store PC) for a job."""
    job = get_job(job_id)
    return job.get("file_url") or job.get("filepath")


def get_job_sender(job_id: str) -> str | None:
    """Return the customer phone for a job."""
    return get_job(job_id).get("sender")


def list_jobs_by_sender(phone: str, limit: int = 20) -> list:
    """A customer's own print orders, newest first — for account order history.

    Keyed on jobs.sender (the customer phone), capped to the most recent `limit`.
    """
    try:
        result = (
            _client().table("jobs")
            .select("job_id,received_at,filename,status,page_count,copies,"
                    "colour,finishing,amount_quoted,amount_collected,"
                    "pickup_code,delivery")
            .eq("sender", phone)
            .order("received_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        logger.error("list_jobs_by_sender error %s: %s", phone, exc)
        return []


def update_job_settings(job_id: str, amount_quoted: float, copies: int,
                        finishing: str, size: str, colour: str, layout: str) -> None:
    """Persist quoted price and print settings onto a job row."""
    try:
        _client().table("jobs").update({
            "amount_quoted": amount_quoted,
            "copies":        copies,
            "finishing":     finishing,
            "size":          size,
            "colour":        colour,
            "layout":        layout,
        }).eq("job_id", job_id).execute()
    except Exception as e:
        logger.error(f"update_job_settings error for {job_id}: {e}")


def update_job_delivery(job_id: str, delivery: int) -> None:
    """Set delivery flag on a single job."""
    try:
        _client().table("jobs").update({"delivery": delivery}).eq("job_id", job_id).execute()
    except Exception as e:
        logger.error(f"update_job_delivery error for {job_id}: {e}")


def update_job_paid(job_id: str, amount: float, method: str, pay_id: str) -> None:
    """Mark a job as Paid and record payment details.

    Idempotently claims a pickup_code for this job if one is not already
    set. Razorpay can fire the same webhook more than once; calling this
    twice for the same job_id will not generate a second pickup code.
    """
    client = _client()

    update_payload: dict[str, object] = {
        "status":              "Paid",
        "amount_collected":    amount,
        "payment_mode":        method,
        "razorpay_payment_id": pay_id,
    }

    try:
        existing = (
            client.table("jobs")
            .select("pickup_code")
            .eq("job_id", job_id)
            .limit(1)
            .execute()
        )
        rows = getattr(existing, "data", None) or []
        existing_code = rows[0].get("pickup_code") if rows else None
        if not existing_code:
            try:
                # Lazy import: a missing pickup_code module must not kill
                # every db_cloud importer.
                from pickup_code import claim_unique_pickup_code
                update_payload["pickup_code"] = claim_unique_pickup_code(client)
            except Exception as e:
                logger.error(
                    f"update_job_paid: could not claim pickup_code for {job_id}: {e}; "
                    "payment status will still be recorded but the customer cannot be "
                    "given a pickup code automatically"
                )
    except Exception as e:
        logger.error(f"update_job_paid: pickup_code lookup failed for {job_id}: {e}")

    # Routing decision (block 3). Gated by MULTISTORE_ROUTING_ENABLED so a
    # bad deploy can be reverted without code rollback — flip the env var
    # to false and the path becomes a no-op.
    if os.environ.get("MULTISTORE_ROUTING_ENABLED", "").lower() in ("1", "true", "yes"):
        try:
            # Lazy import: a missing routing/ package must not kill every
            # db_cloud importer.
            from routing.engine import (
                JobSpec,
                decide as _routing_decide,
                load_eligible_partners as _routing_load_partners,
                record_decision as _routing_record,
            )
            candidates = _routing_load_partners(client)
            spec = JobSpec(job_id=job_id)
            decision = _routing_decide(spec, candidates)
            _routing_record(client, decision)
            if decision.chosen_store_id:
                update_payload["assigned_store_id"] = decision.chosen_store_id
                # Block 5: dispatch the job to the chosen store's owner.
                # Best-effort — a dispatch failure does not block payment
                # status from being recorded. The store dispatcher uses
                # WhatsApp; no software install required at the store.
                try:
                    from store_dispatch import dispatch_job
                    chosen = next(
                        (c for c in candidates
                         if c.store_id == decision.chosen_store_id),
                        None,
                    )
                    if chosen is not None:
                        job_after = (
                            client.table("jobs")
                            .select("job_id,pickup_code,customer_name,"
                                    "page_count,copies,colour,size,"
                                    "finishing,file_url")
                            .eq("job_id", job_id)
                            .limit(1)
                            .execute()
                        )
                        rows_after = getattr(job_after, "data", None) or []
                        job_row = rows_after[0] if rows_after else {"job_id": job_id}
                        if "pickup_code" in update_payload:
                            job_row["pickup_code"] = update_payload["pickup_code"]
                        partner_rows = (
                            client.table("partners")
                            .select("store_id,dispatch_whatsapp")
                            .eq("store_id", chosen.store_id)
                            .limit(1)
                            .execute()
                        )
                        prows = getattr(partner_rows, "data", None) or []
                        partner_row = prows[0] if prows else {
                            "store_id": chosen.store_id,
                            "dispatch_whatsapp": "",
                        }
                        file_url = job_row.get("file_url") or ""
                        dispatch_job(job_row, partner_row, file_url)
                except Exception as e:
                    logger.error(
                        f"update_job_paid: dispatch failed for {job_id}: {e}"
                    )
        except Exception as e:
            logger.error(f"update_job_paid: routing failed for {job_id}: {e}")

    try:
        client.table("jobs").update(update_payload).eq("job_id", job_id).execute()
    except Exception as e:
        logger.error(f"update_job_paid error for {job_id}: {e}")


def update_jobs_payment_link(job_ids: list, link_id: str, link_sent_at: str) -> None:
    """Set the Razorpay link ID on multiple jobs (single batched query)."""
    if not job_ids:
        return
    try:
        _client().table("jobs").update({
            "razorpay_link_id": link_id,
            "link_sent_at":     link_sent_at,
        }).in_("job_id", list(job_ids)).execute()
    except Exception as e:
        logger.error(f"update_jobs_payment_link error: {e}")


def insert_job_from_webhook(job_id: str, sender: str, filename: str,
                            file_url: str) -> None:
    """Insert a new Pending job row when a file arrives via WhatsApp webhook."""
    try:
        _client().table("jobs").upsert({
            "job_id":      job_id,
            "sender":      sender,
            "filename":    filename,
            "file_url":    file_url,
            "status":      "Pending",
            "received_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, on_conflict="job_id").execute()
    except Exception as e:
        logger.error(f"insert_job_from_webhook error for {job_id}: {e}")


# ── job_batches ───────────────────────────────────────────────────────────────

def get_batch(batch_id: str) -> dict:
    """Fetch a job batch row."""
    try:
        result = _client().table("job_batches").select("*").eq("batch_id", batch_id).execute()
        return result.data[0] if result.data else {}
    except Exception as e:
        logger.error(f"get_batch error for {batch_id}: {e}")
        return {}


def update_batch_payment(batch_id: str, total_amount: float,
                         link_id: str, link_sent_at: str) -> None:
    """Record payment link details on a batch."""
    try:
        _client().table("job_batches").update({
            "total_amount":     total_amount,
            "razorpay_link_id": link_id,
            "link_sent_at":     link_sent_at,
            "status":           "awaiting_payment",
        }).eq("batch_id", batch_id).execute()
    except Exception as e:
        logger.error(f"update_batch_payment error for {batch_id}: {e}")


def update_batch_paid(batch_id: str) -> None:
    """Mark a batch as paid."""
    try:
        _client().table("job_batches").update(
            {"status": "paid"}
        ).eq("batch_id", batch_id).execute()
    except Exception as e:
        logger.error(f"update_batch_paid error for {batch_id}: {e}")


# ── job_reviews ───────────────────────────────────────────────────────────────

def get_pending_review(phone: str) -> dict | None:
    """Return a pending (unsent rating) review row for a customer, or None."""
    try:
        result = (
            _client().table("job_reviews")
            .select("id")
            .eq("phone", phone)
            .is_("rating", "null")
            .eq("review_sent", True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"get_pending_review error for {phone}: {e}")
        return None


# ── Supabase Storage (replaces C:\Printosky\Jobs\Incoming\) ──────────────────

INCOMING_BUCKET = "incoming-files"


def upload_file(filename: str, content: bytes, mime_type: str) -> str:
    """
    Upload a customer file to Supabase Storage.
    Returns the public URL (store PC polls Supabase and downloads from here).
    """
    try:
        _client().storage.from_(INCOMING_BUCKET).upload(
            path=filename,
            file=content,
            file_options={"content-type": mime_type, "upsert": "true"},
        )
        return _client().storage.from_(INCOMING_BUCKET).get_public_url(filename)
    except Exception as e:
        logger.error(f"upload_file error for {filename}: {e}")
        return ""


# ── conversation_log ──────────────────────────────────────────────────────────

def log_message(phone: str, direction: str, body: str,
                message_type: str = "text", filename: str | None = None,
                job_id: str | None = None,
                media_url: str | None = None) -> None:
    """Insert a row into conversation_log. Silent on error — never raises."""
    try:
        _client().table("conversation_log").insert({
            "phone":        phone,
            "direction":    direction,
            "message_type": message_type,
            "body":         (body or "")[:2000],
            "filename":     filename,
            "job_id":       job_id,
            "media_url":    media_url,
        }).execute()
    except Exception as e:
        logger.warning(f"log_message error ({direction} {phone}): {e}")


# ── WhatsApp (Meta) per-message cost tracking ─────────────────────────────────
# Meta never returns a money amount, but each outbound message's status callback
# carries a `pricing` object: category (service / marketing / utility /
# authentication) + a `billable` flag. We honour `billable` (service and
# in-window utility are free) and apply this rate card to ESTIMATE the INR spend.
# VERIFY rates against Meta's current India price list — they change; only the
# `billable` flag from Meta is authoritative for whether a message is charged.
WA_RATE_CARD_INR: dict[str, float] = {
    "marketing":      0.78,
    "utility":        0.12,
    "authentication": 0.12,
    "service":        0.00,
}


def wa_estimated_cost_inr(category: str | None, billable: bool | None) -> float:
    """Estimated INR for one message given Meta's category + billable flag."""
    if not billable:
        return 0.0
    return float(WA_RATE_CARD_INR.get((category or "").lower(), 0.0))


def record_wa_message_cost(wamid: str, recipient: str | None, status: str | None,
                           pricing: dict | None = None,
                           conversation: dict | None = None) -> None:
    """Upsert a per-message WhatsApp cost row from a Meta status callback.

    Keyed by wamid; called for every status (sent / delivered / read / failed).
    The `pricing` object usually arrives on 'sent'. Cost fields are written only
    when `pricing` is present, so a later status without pricing never clears an
    earlier estimate. Silent on error — cost telemetry must never break the
    webhook.
    """
    if not wamid:
        return
    row: dict = {
        "wamid":      wamid,
        "recipient":  recipient,
        "status":     status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if pricing:
        category = pricing.get("category")
        billable = pricing.get("billable")
        row["category"]      = category
        row["billable"]      = billable
        row["pricing_model"] = pricing.get("pricing_model")
        row["est_cost_inr"]  = wa_estimated_cost_inr(category, billable)
    if conversation:
        row["conversation_id"] = conversation.get("id")
        row["origin_type"]     = (conversation.get("origin") or {}).get("type")
    try:
        _client().table("wa_message_costs").upsert(row, on_conflict="wamid").execute()
    except Exception as exc:
        logger.warning("record_wa_message_cost error for %s: %s", wamid, exc)


# The webhook logs the bot's auto-reply a few hundred ms BEFORE the inbound that
# triggered it (clock skew between Meta's inbound timestamp and the server's
# outbound send time). A strict "outbound after inbound" check therefore flags
# immediately-answered messages as breaches. Treat an outbound within this many
# seconds before the inbound (or any time after) as a reply.
SLA_REPLY_TOLERANCE_SECONDS = 120


def _compute_sla_breaches(rows, now, threshold_hours: int = 1,
                          reply_tolerance_seconds: int = SLA_REPLY_TOLERANCE_SECONDS) -> list[dict]:
    """Pure breach computation from conversation_log rows (order-independent).

    A breach = a phone whose newest inbound is older than `threshold_hours` and
    has no outbound reply within `reply_tolerance_seconds` before it (or any time
    after). `now` and parsed timestamps are timezone-aware UTC.
    """
    from datetime import datetime, timedelta

    def _parse(ts):
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            return None

    latest: dict[str, dict] = {}
    for r in rows:
        ph = r.get("phone")
        if not ph:
            continue
        dt = _parse(r.get("created_at"))
        if dt is None:
            continue
        slot = latest.setdefault(ph, {})
        d = r.get("direction")
        if d == "inbound":
            if "in" not in slot or dt > slot["in"]:
                slot["in"] = dt
        elif d == "outbound":
            if "out" not in slot or dt > slot["out"]:
                slot["out"] = dt

    threshold = now - timedelta(hours=threshold_hours)
    tol = timedelta(seconds=reply_tolerance_seconds)
    breaches: list[dict] = []
    for ph, slot in latest.items():
        last_in = slot.get("in")
        if last_in is None:
            continue
        last_out = slot.get("out")
        if last_out is not None and last_out > last_in - tol:
            continue                       # replied (allowing for the logging artifact)
        if last_in > threshold:
            continue                       # not yet older than threshold_hours
        breaches.append({"phone": ph, "last_inbound_at": last_in.isoformat()})
    return breaches


def find_sla_breaches(threshold_hours: int = 1,
                      alert_cooldown_hours: int = 6,
                      lookback_hours: int = 48) -> list[dict]:
    """Return customers whose newest inbound > threshold_hours has no later reply.

    Returns a list of {"phone", "last_inbound_at"} dicts, excluding any phone
    that was already alerted within `alert_cooldown_hours` (cooldown to avoid
    repeating the same nag).
    """
    from datetime import datetime, timezone, timedelta
    try:
        client = _client()
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
        rows = (
            client.table("conversation_log")
            .select("phone,direction,created_at")
            .gte("created_at", cutoff_iso)
            .order("created_at", desc=True)
            .limit(2000)
            .execute()
            .data
            or []
        )

        breaches = _compute_sla_breaches(rows, datetime.now(timezone.utc), threshold_hours)

        if not breaches or alert_cooldown_hours <= 0:
            return breaches

        cooldown_iso = (datetime.now(timezone.utc)
                        - timedelta(hours=alert_cooldown_hours)).isoformat()
        contacts = (
            client.table("whatsapp_contacts")
            .select("phone,last_sla_alert_at")
            .in_("phone", [b["phone"] for b in breaches])
            .execute()
            .data
            or []
        )
        recently_alerted = {
            c["phone"] for c in contacts
            if c.get("last_sla_alert_at") and c["last_sla_alert_at"] > cooldown_iso
        }
        return [b for b in breaches if b["phone"] not in recently_alerted]
    except Exception as exc:
        logger.error("find_sla_breaches error: %s", exc)
        return []


def mark_sla_alerted(phone: str) -> None:
    """Stamp last_sla_alert_at=now on a contact; silent on error."""
    try:
        _client().table("whatsapp_contacts").upsert(
            {"phone": phone,
             "last_sla_alert_at": datetime.now(timezone.utc).isoformat()},
            on_conflict="phone",
        ).execute()
    except Exception as exc:
        logger.warning("mark_sla_alerted error for %s: %s", phone, exc)


def activity_counts(hours: int = 24) -> dict:
    """Count recent activity for the daily liveness check.

    Returns {"inbound": N, "jobs": M, "hours": hours}. `inbound` is the number
    of inbound rows in conversation_log within the window — the strongest
    "is the WhatsApp webhook alive?" signal, since a live shop never goes a
    full day with zero inbound messages even if no print jobs are created.
    `jobs` is best-effort (jobs.received_at is naive IST text, so it's compared
    lexically against an IST wall-clock cutoff). On error each count is -1 so
    the caller can tell "query failed" apart from a genuine zero.
    """
    from datetime import datetime, timezone, timedelta

    out = {"inbound": -1, "jobs": -1, "hours": hours}
    client = _client()

    try:
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        r = (
            client.table("conversation_log")
            .select("phone", count="exact")
            .eq("direction", "inbound")
            .gte("created_at", cutoff_iso)
            .limit(1)
            .execute()
        )
        cnt = getattr(r, "count", None)
        out["inbound"] = cnt if cnt is not None else len(r.data or [])
    except Exception as exc:
        logger.warning("activity_counts inbound error: %s", exc)

    try:
        # jobs.received_at is naive IST wall-clock text "YYYY-MM-DD HH:MM:SS".
        ist_cutoff = (
            datetime.now(timezone.utc) + timedelta(hours=5, minutes=30) - timedelta(hours=hours)
        ).strftime("%Y-%m-%d %H:%M:%S")
        r = (
            client.table("jobs")
            .select("job_id", count="exact")
            .gte("received_at", ist_cutoff)
            .limit(1)
            .execute()
        )
        cnt = getattr(r, "count", None)
        out["jobs"] = cnt if cnt is not None else len(r.data or [])
    except Exception as exc:
        logger.warning("activity_counts jobs error: %s", exc)

    return out


def is_new_contact(phone: str) -> bool:
    """True if there is no prior conversation history for this phone.

    Used to greet genuinely new customers exactly once. The webhook logs the
    current inbound message *after* routing, so a first-ever message sees zero
    rows here. On any error we return False — better silent than spamming.
    """
    try:
        result = (
            _client().table("conversation_log")
            .select("phone", count="exact")
            .eq("phone", phone)
            .limit(1)
            .execute()
        )
        count = getattr(result, "count", None)
        if count is not None:
            return count == 0
        return not (result.data or [])
    except Exception as e:
        logger.error(f"is_new_contact error for {phone}: {e}")
        return False


# ── WhatsApp contacts ─────────────────────────────────────────────────────────

def get_media_url(path: str) -> str:
    """Return the public URL for a file stored in Supabase Storage.

    The incoming-files bucket is public, so this constructs a stable URL.
    If the bucket ever goes private, change this to create_signed_url().
    """
    return _client().storage.from_(INCOMING_BUCKET).get_public_url(path)


# ── Project Builder orders ────────────────────────────────────────────────────

PB_ORDERS_PREFIX = "project-builder/orders"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def upload_pb_doc(order_id: str, docx_bytes: bytes) -> str:
    """Upload a generated project builder DOCX to Supabase Storage.

    Stored under incoming-files/project-builder/orders/{order_id}.docx.
    The incoming-files bucket is public so the returned URL is permanent.
    """
    path = f"{PB_ORDERS_PREFIX}/{order_id}.docx"
    try:
        _client().storage.from_(INCOMING_BUCKET).upload(
            path=path,
            file=docx_bytes,
            file_options={"content-type": _DOCX_MIME, "upsert": "true"},
        )
        return _client().storage.from_(INCOMING_BUCKET).get_public_url(path)
    except Exception as e:
        logger.error(f"upload_pb_doc error for {order_id}: {e}")
        return ""


def save_pb_order(order_id: str, tier: str, university: str,
                  whatsapp_phone: str, student_name: str,
                  razorpay_order_id: str, razorpay_payment_id: str,
                  amount_inr: int, storage_path: str, download_url: str) -> bool:
    """Insert a project builder order into project_builder_orders table."""
    try:
        _client().table("project_builder_orders").insert({
            "id":                  order_id,
            "tier":                tier,
            "university":          university,
            "whatsapp_phone":      whatsapp_phone,
            "student_name":        student_name,
            "razorpay_order_id":   razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "amount_inr":          amount_inr,
            "storage_path":        storage_path,
            "download_url":        download_url,
            "status":              "delivered",
        }).execute()
        return True
    except Exception as e:
        logger.error(f"save_pb_order error for {order_id}: {e}")
        return False


def get_pb_order(order_id: str, whatsapp_phone: str | None = None) -> dict | None:
    """Fetch a project builder order by ID, optionally verifying the phone number."""
    try:
        q = _client().table("project_builder_orders").select("*").eq("id", order_id)
        if whatsapp_phone:
            digits = "".join(c for c in whatsapp_phone if c.isdigit())
            if len(digits) == 10:
                digits = "91" + digits
            q = q.eq("whatsapp_phone", digits)
        res = q.limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"get_pb_order error: {e}")
        return None


def list_pb_orders(limit: int = 100) -> list:
    """List all project builder orders newest-first (admin use)."""
    try:
        res = (
            _client().table("project_builder_orders")
            .select("id,tier,university,whatsapp_phone,student_name,amount_inr,status,created_at,download_url")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error(f"list_pb_orders error: {e}")
        return []


# ---------------------------------------------------------------------------
# Project Builder operator queue (P0 Day 2.5)
# ---------------------------------------------------------------------------
# Failed-AI orders land here. An operator picks the row up from the admin
# dashboard, finishes the DOCX manually, uploads, and the customer is
# WhatsApped the download link. SLA = 6h from created_at.
# Table schema: api/migrations/SCHEMA_v18_pb_operator_queue.sql

PB_OPERATOR_QUEUE_PREFIX = "project-builder/operator-delivered"


def enqueue_operator_job(
    *,
    pb_order_id: str | None,
    customer_phone: str,
    student_name: str,
    university: str,
    tier: str,
    input_text: str,
    sonnet_partial: dict | None = None,
    opus_partial: dict | None = None,
    last_model_used: str = "",
    validation_errors: list[str] | None = None,
) -> str | None:
    """Insert a failed-AI order into pb_operator_queue. Returns the new
    queue row's UUID on success, None on failure (logged).
    """
    try:
        ai_attempts: list[dict] = []
        if sonnet_partial is not None:
            ai_attempts.append({"model": "sonnet", "structure": sonnet_partial})
        if opus_partial is not None:
            ai_attempts.append({"model": "opus",   "structure": opus_partial})
        if validation_errors:
            ai_attempts.append({
                "model":  last_model_used or "unknown",
                "errors": list(validation_errors),
            })

        row = {
            "pb_order_id":              pb_order_id,
            "customer_phone":           customer_phone,
            "student_name":             student_name,
            "university":               university,
            "tier":                     tier,
            "input_text":               input_text,
            "input_size_bytes":         len(input_text.encode("utf-8")) if input_text else 0,
            "ai_attempts":              ai_attempts,
            "sonnet_partial_structure": sonnet_partial,
            "opus_partial_structure":   opus_partial,
            "last_model_used":          last_model_used,
            # status, deadline_ts default to 'pending' + now+6h via DEFAULTs
        }
        res = _client().table("pb_operator_queue").insert(row).execute()
        if res.data:
            return res.data[0].get("id")
        return None
    except Exception as e:
        logger.error(f"enqueue_operator_job error: {e}")
        return None


def list_operator_queue(
    *,
    status: str | None = "pending",
    limit: int = 100,
) -> list:
    """List operator queue rows by deadline (most urgent first). Pass
    status=None to get every row regardless of state.
    """
    try:
        q = (
            _client().table("pb_operator_queue")
            .select("id,pb_order_id,customer_phone,student_name,university,tier,"
                     "input_size_bytes,last_model_used,status,assigned_to,"
                     "claimed_at,deadline_ts,delivered_at,created_at,"
                     "delivered_download_url")
            .order("deadline_ts", desc=False)
            .limit(limit)
        )
        if status is not None:
            q = q.eq("status", status)
        res = q.execute()
        return res.data or []
    except Exception as e:
        logger.error(f"list_operator_queue error: {e}")
        return []


def get_operator_job(queue_id: str) -> dict | None:
    """Fetch a single operator queue row including input_text and partials."""
    try:
        res = (
            _client().table("pb_operator_queue")
            .select("*")
            .eq("id", queue_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"get_operator_job error: {e}")
        return None


def claim_operator_job(queue_id: str, operator: str) -> bool:
    """Mark an operator queue row as claimed. Optimistic — only succeeds
    if the row is currently 'pending'. Refuses to re-claim.
    """
    from datetime import datetime, timezone
    try:
        res = (
            _client().table("pb_operator_queue")
            .update({
                "status":      "claimed",
                "assigned_to": operator,
                "claimed_at":  datetime.now(timezone.utc).isoformat(),
            })
            .eq("id", queue_id)
            .eq("status", "pending")
            .execute()
        )
        return bool(res.data)
    except Exception as e:
        logger.error(f"claim_operator_job error: {e}")
        return False


def deliver_operator_job(queue_id: str, docx_bytes: bytes) -> tuple[bool, str]:
    """Upload the operator-finished DOCX, mark the row delivered, return
    (success, public_url). The caller handles WhatsApp delivery
    notification — keeps this layer DB-only.
    """
    from datetime import datetime, timezone
    try:
        path = f"{PB_OPERATOR_QUEUE_PREFIX}/{queue_id}.docx"
        _client().storage.from_(INCOMING_BUCKET).upload(
            path=path,
            file=docx_bytes,
            file_options={"content-type": _DOCX_MIME, "upsert": "true"},
        )
        url = _client().storage.from_(INCOMING_BUCKET).get_public_url(path)

        _client().table("pb_operator_queue").update({
            "status":                 "delivered",
            "delivered_at":           datetime.now(timezone.utc).isoformat(),
            "delivered_docx_path":    path,
            "delivered_download_url": url,
        }).eq("id", queue_id).execute()
        return True, url
    except Exception as e:
        logger.error(f"deliver_operator_job error: {e}")
        return False, ""


def get_operator_queue_depth() -> dict:
    """Return the current backlog size by status. Used by the admin
    dashboard SLA gauge and Premium-tier auto-pause logic.
    """
    from datetime import datetime, timezone
    try:
        res = (
            _client().table("pb_operator_queue")
            .select("status,deadline_ts,tier")
            .in_("status", ["pending", "claimed"])
            .execute()
        )
        rows = res.data or []
        now = datetime.now(timezone.utc)
        pending = sum(1 for r in rows if r.get("status") == "pending")
        claimed = sum(1 for r in rows if r.get("status") == "claimed")
        over_sla = 0
        for r in rows:
            try:
                dl = r.get("deadline_ts")
                if dl and datetime.fromisoformat(dl.replace("Z", "+00:00")) < now:
                    over_sla += 1
            except Exception:
                pass
        premium_in_q = sum(1 for r in rows
                            if r.get("tier") in ("premium", "luxury"))
        return {
            "pending":      pending,
            "claimed":      claimed,
            "total_open":   pending + claimed,
            "over_sla":     over_sla,
            "premium_in_q": premium_in_q,
        }
    except Exception as e:
        logger.error(f"get_operator_queue_depth error: {e}")
        return {"pending": 0, "claimed": 0, "total_open": 0,
                "over_sla": 0, "premium_in_q": 0, "_error": str(e)}


def upsert_contact(phone: str, name: str | None = None) -> None:
    """Insert or update a WhatsApp contact. Name only written when provided."""
    data: dict = {"phone": phone}
    if name:
        data["name"] = name
    try:
        _client().table("whatsapp_contacts").upsert(
            data, on_conflict="phone"
        ).execute()
    except Exception as exc:
        logger.warning("upsert_contact failed: %s", exc)


def mark_contact_seen(phone: str) -> None:
    """Set last_seen_at = now() for a contact (called when staff opens the thread).

    Uses upsert so a contact row is created if it doesn't exist yet.
    """
    try:
        from datetime import datetime, timezone
        _client().table("whatsapp_contacts").upsert(
            {"phone": phone, "last_seen_at": datetime.now(timezone.utc).isoformat()},
            on_conflict="phone",
        ).execute()
    except Exception as exc:
        logger.warning("mark_contact_seen failed: %s", exc)


# ── book_orders (Xtraa book campaign) ─────────────────────────────────────────

# Statuses considered "in progress" — a new enquiry resumes/uses these rather
# than starting a fresh order. confirmed/cancelled are terminal.
BOOK_ACTIVE_STATUSES = ("collecting", "awaiting_payment", "payment_review", "partially_paid")
BOOK_PROOF_PREFIX = "book-payments"


def get_active_book_order(phone: str) -> dict:
    """Return the most recent in-progress book order for this phone, or {}."""
    try:
        result = (
            _client().table("book_orders")
            .select("*")
            .eq("phone", phone)
            .in_("status", list(BOOK_ACTIVE_STATUSES))
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else {}
    except Exception as exc:
        logger.error("get_active_book_order error for %s: %s", phone, exc)
        return {}


def get_dispatched_book_order(phone: str) -> dict:
    """Return the most recent dispatched/delivered book order for this phone, or {}.

    Used to re-share tracking info when a customer asks about an order that has
    already shipped (distinct from get_active_book_order, which is in-progress).
    """
    try:
        result = (
            _client().table("book_orders")
            .select("*")
            .eq("phone", phone)
            .in_("status", ["dispatched", "delivered"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else {}
    except Exception as exc:
        logger.error("get_dispatched_book_order error for %s: %s", phone, exc)
        return {}


def create_book_order(order_code: str, phone: str, name: str | None = None) -> dict:
    """Insert a new 'collecting' book order. Returns the created row, or {}."""
    try:
        result = (
            _client().table("book_orders")
            .insert({
                "order_code": order_code,
                "phone":      phone,
                "name":       name,
                "status":     "collecting",
            })
            .execute()
        )
        return result.data[0] if result.data else {}
    except Exception as exc:
        logger.error("create_book_order error for %s: %s", phone, exc)
        return {}


def update_book_order(order_code: str, **fields) -> bool:
    """Update fields on a book order by order_code.

    Returns True on success, False on failure (failure is also logged).
    Callers that must guarantee a state change (e.g. confirm) should check the
    return value and/or re-read the row — never assume success.
    """
    if not fields:
        return False
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        _client().table("book_orders").update(fields).eq("order_code", order_code).execute()
        return True
    except Exception as exc:
        logger.error("update_book_order error for %s: %s", order_code, exc)
        return False


def get_book_order(order_code: str) -> dict:
    """Fetch a single book order by code, or {}."""
    try:
        result = (
            _client().table("book_orders")
            .select("*")
            .eq("order_code", order_code)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else {}
    except Exception as exc:
        logger.error("get_book_order error for %s: %s", order_code, exc)
        return {}


def list_book_orders(status: str | None = None, limit: int = 100) -> list:
    """List book orders, newest first. Optionally filter by status."""
    try:
        q = _client().table("book_orders").select("*")
        if status:
            q = q.eq("status", status)
        result = q.order("created_at", desc=True).limit(limit).execute()
        return result.data or []
    except Exception as exc:
        logger.error("list_book_orders error: %s", exc)
        return []


def create_walk_in_order(order_code: str, name: str | None, phone: str | None,
                         address: str | None, items: dict,
                         books_total: float, courier: float, grand_total: float,
                         payment_mode: str, status: str,
                         commission: float = 0.0,
                         payment_collected_by: str = "oxygen",
                         delivery_method: str = "courier",
                         via_divya: bool = True,
                         source: str = "walk_in") -> dict:
    """Insert a manually-created book order (walk-in / in-store, or Divya-via-Anu).

    Returns the inserted row. `source` distinguishes 'walk_in' from 'divya'.
    `commission` is the ₹50/book owed to Divya teacher; `payment_collected_by`
    is 'oxygen' | 'divya' | 'pending' and drives the settlement ledger.
    """
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "order_code":   order_code,
        "phone":        phone or "",
        "name":         name,
        "items":        items,
        "books_total":  books_total,
        "courier":      courier,
        "grand_total":  grand_total,
        "address":      address,
        "contact_phone": phone,
        "status":       status,
        "payment_mode": payment_mode,
        "source":       source,
        "commission":   commission,
        "payment_collected_by": payment_collected_by,
        "delivery_method":      delivery_method,
        "via_divya":    via_divya,
        "confirmed_at": now,
    }
    if status == "delivered":
        row["delivered_at"] = now
    try:
        result = _client().table("book_orders").insert(row).execute()
        return result.data[0] if result.data else {}
    except Exception as exc:
        logger.error("create_walk_in_order error: %s", exc)
        return {}


# Statuses that count as a real (sold) order for the Divya commission ledger.
DIVYA_LEDGER_STATUSES = ("confirmed", "dispatched", "delivered")


def divya_ledger(include_settled: bool = False,
                 date_from: str | None = None,
                 date_to: str | None = None) -> dict:
    """Aggregate the Divya teacher commission ledger.

    Counts only sold orders (status in DIVYA_LEDGER_STATUSES) attributed to
    Divya, optionally restricted to created_at in [date_from, date_to)
    (ISO-8601 strings — used for daily/weekly/monthly summaries). Returns
    headline totals, the full `orders` list for the period (for CSV/PDF export),
    and the `unsettled` subset tagged with who owes whom:
        * payment_collected_by in ('oxygen','pending') -> Oxygen owes Divya the
          commission.
        * payment_collected_by == 'divya'              -> Divya owes Oxygen
          (grand_total - commission).
    `net` > 0 means Divya pays Oxygen; < 0 means Oxygen pays Divya.
    """
    empty = {"total_orders": 0, "total_books": 0, "total_commission": 0.0,
             "oxygen_owes_divya": 0.0, "divya_owes_oxygen": 0.0, "net": 0.0,
             "orders": [], "unsettled": []}
    try:
        q = (
            _client().table("book_orders")
            .select("order_code,name,items,grand_total,commission,"
                    "payment_collected_by,delivery_method,divya_settled,status,created_at")
            .eq("via_divya", True)
            .in_("status", list(DIVYA_LEDGER_STATUSES))
        )
        if date_from:
            q = q.gte("created_at", date_from)
        if date_to:
            q = q.lt("created_at", date_to)
        rows = q.order("created_at", desc=True).execute().data or []
    except Exception as exc:
        logger.error("divya_ledger error: %s", exc)
        return empty

    total_books = total_commission = 0
    oxygen_owes = divya_owes = 0.0
    orders, unsettled = [], []
    for r in rows:
        comm = float(r.get("commission") or 0)
        gt = float(r.get("grand_total") or 0)
        items = r.get("items") or {}
        books = sum(int(v) for k, v in items.items()
                    if k in ("malayalam", "hindi", "english") and v)
        total_books += books
        total_commission += comm
        collected = r.get("payment_collected_by") or "oxygen"
        settled = bool(r.get("divya_settled"))
        if collected == "divya":
            amount, direction = gt - comm, "divya_owes_oxygen"
        else:  # 'oxygen' or 'pending'
            amount, direction = comm, "oxygen_owes_divya"
        entry = {
            "order_code":   r.get("order_code"),
            "name":         r.get("name"),
            "books":        books,
            "grand_total":  gt,
            "commission":   comm,
            "collected_by": collected,
            "direction":    direction,
            "amount":       amount,
            "settled":      settled,
            "created_at":   r.get("created_at"),
        }
        orders.append(entry)
        if settled and not include_settled:
            continue
        if direction == "divya_owes_oxygen":
            divya_owes += amount
        else:
            oxygen_owes += amount
        unsettled.append(entry)
    return {
        "total_orders":      len(rows),
        "total_books":       total_books,
        "total_commission":  float(total_commission),
        "oxygen_owes_divya": oxygen_owes,
        "divya_owes_oxygen": divya_owes,
        "net":               divya_owes - oxygen_owes,
        "orders":            orders,
        "unsettled":         unsettled,
    }


def mark_divya_settled(order_code: str, settled: bool = True) -> None:
    """Mark (or unmark) a Divya order's commission as reconciled. Silent on error."""
    now = datetime.now(timezone.utc).isoformat()
    fields = {
        "divya_settled":    settled,
        "divya_settled_at": now if settled else None,
        "updated_at":       now,
    }
    try:
        _client().table("book_orders").update(fields).eq("order_code", order_code).execute()
    except Exception as exc:
        logger.error("mark_divya_settled error for %s: %s", order_code, exc)


def log_llm_cost(engine: str, model: str, input_tokens: int, output_tokens: int,
                 cost_usd: float, cost_inr: float, elapsed_ms: int | None = None,
                 error: str | None = None) -> None:
    """Record one Claude API call's cost into pb_api_calls telemetry. Best-effort.

    Reuses the existing pb_api_calls table so spend is queryable, e.g.:
        select count(*), sum(cost_inr) from pb_api_calls where engine = 'anu_parser';
    """
    import uuid
    try:
        _client().table("pb_api_calls").insert({
            "job_token":     str(uuid.uuid4()),
            "engine":        engine,
            "model":         model,
            "input_tokens":  int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "cost_usd":      round(float(cost_usd or 0), 6),
            "cost_inr":      round(float(cost_inr or 0), 4),
            "elapsed_ms":    elapsed_ms,
            "error":         error,
        }).execute()
    except Exception as exc:
        logger.warning("log_llm_cost failed (best-effort): %s", exc)


def find_abandoned_book_carts(idle_hours: int = 2, window_hours: int = 24,
                              limit: int = 100) -> list:
    """Return open book carts that have gone quiet but are still messageable.

    A cart qualifies when its status is still open (collecting / awaiting_payment),
    it hasn't been touched for `idle_hours`, its last activity is within
    `window_hours` (so a free-form WhatsApp message is still allowed by Meta's
    24-hour rule), and it hasn't already been reminded.
    """
    try:
        now        = datetime.now(timezone.utc)
        idle_cut   = (now - timedelta(hours=idle_hours)).isoformat()
        window_cut = (now - timedelta(hours=window_hours)).isoformat()
        rows = (
            _client().table("book_orders")
            .select("order_code,phone,items,status,updated_at")
            .in_("status", ["collecting", "awaiting_payment"])
            .is_("abandoned_reminder_at", "null")
            .lt("updated_at", idle_cut)
            .gt("updated_at", window_cut)
            .order("updated_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )
        return rows
    except Exception as exc:
        logger.error("find_abandoned_book_carts error: %s", exc)
        return []


def mark_abandoned_reminded(order_code: str) -> None:
    """Stamp abandoned_reminder_at=now (without bumping updated_at). Silent on error."""
    try:
        _client().table("book_orders").update(
            {"abandoned_reminder_at": datetime.now(timezone.utc).isoformat()}
        ).eq("order_code", order_code).execute()
    except Exception as exc:
        logger.warning("mark_abandoned_reminded error for %s: %s", order_code, exc)


def upload_book_payment_proof(order_code: str, content: bytes, mime_type: str) -> str:
    """Upload a payment screenshot to storage. Returns its public URL, or ''."""
    ext = ".jpg"
    if "png" in (mime_type or ""):
        ext = ".png"
    ts   = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    path = f"{BOOK_PROOF_PREFIX}/{order_code}_{ts}{ext}"
    try:
        _client().storage.from_(INCOMING_BUCKET).upload(
            path=path,
            file=content,
            file_options={"content-type": mime_type or "image/jpeg", "upsert": "true"},
        )
        return _client().storage.from_(INCOMING_BUCKET).get_public_url(path)
    except Exception as exc:
        logger.error("upload_book_payment_proof error for %s: %s", order_code, exc)
        return ""


# ── book part-payment ledger (book_payments) ──────────────────────────────────

def add_book_payment(order_code: str, proof_url: str | None) -> dict:
    """Insert a pending payment row (one screenshot). Returns the row, or {}."""
    try:
        result = (
            _client().table("book_payments")
            .insert({"order_code": order_code, "proof_url": proof_url,
                     "status": "pending"})
            .execute()
        )
        return result.data[0] if result.data else {}
    except Exception as exc:
        logger.error("add_book_payment error for %s: %s", order_code, exc)
        return {}


def get_book_payment(payment_id) -> dict:
    """Fetch a single payment row by id, or {}."""
    try:
        result = (
            _client().table("book_payments").select("*")
            .eq("id", payment_id).limit(1).execute()
        )
        return result.data[0] if result.data else {}
    except Exception as exc:
        logger.error("get_book_payment error for %s: %s", payment_id, exc)
        return {}


def get_book_payments(order_code: str) -> list:
    """All payment rows for an order, oldest first."""
    try:
        result = (
            _client().table("book_payments").select("*")
            .eq("order_code", order_code).order("created_at").execute()
        )
        return result.data or []
    except Exception as exc:
        logger.error("get_book_payments error for %s: %s", order_code, exc)
        return []


def verify_book_payment(payment_id, amount: float) -> dict:
    """Mark a payment verified with the given amount. Returns the updated row."""
    from datetime import datetime, timezone
    try:
        _client().table("book_payments").update({
            "status": "verified", "amount": amount,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", payment_id).execute()
        return get_book_payment(payment_id)
    except Exception as exc:
        logger.error("verify_book_payment error for %s: %s", payment_id, exc)
        return {}


def reject_book_payment(payment_id) -> bool:
    """Mark a payment rejected (screenshot not a valid/received payment)."""
    from datetime import datetime, timezone
    try:
        _client().table("book_payments").update({
            "status": "rejected",
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", payment_id).execute()
        return True
    except Exception as exc:
        logger.error("reject_book_payment error for %s: %s", payment_id, exc)
        return False


def book_amount_paid(order_code: str) -> float:
    """Sum of VERIFIED payments for an order (the running paid total)."""
    try:
        result = (
            _client().table("book_payments").select("amount")
            .eq("order_code", order_code).eq("status", "verified").execute()
        )
        return float(sum((row.get("amount") or 0) for row in (result.data or [])))
    except Exception as exc:
        logger.error("book_amount_paid error for %s: %s", order_code, exc)
        return 0.0


# ── notes marketplace ─────────────────────────────────────────────────────────
# Schema: SCHEMA_v28_notes_marketplace.sql
# Storage: private "notes" bucket (PDFs) + public INCOMING_BUCKET notes-preview/ (PNGs)

NOTES_BUCKET: str = "notes"

# Pricing constants — use these everywhere; never hardcode the values.
NOTE_PRINT_PRICE_PAISE: int = 100    # ₹1.00 per page = 100 paise
NOTE_CREDIT_PAISE_PER_PAGE: int = 10  # 10% of ₹1 = ₹0.10 = 10 paise per page printed


def create_note(
    note_code: str,
    title: str,
    category: str,
    subject: str,
    page_count: int,
    uploader_phone: str | None = None,
    uploader_email: str | None = None,
    storage_path: str | None = None,
    attests: bool = True,
) -> dict:
    """Insert a new note row with status='pending'. Returns inserted dict or {}.

    Either uploader_phone (WhatsApp auth) or uploader_email (Google/email auth)
    must be provided — the DB CHECK constraint enforces this.
    """
    row: dict = {
        "note_code": note_code,
        "title": title,
        "category": category,
        "subject": subject,
        "page_count": page_count,
        "storage_path": storage_path,
        "uploader_attests": attests,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if uploader_phone:
        row["uploader_phone"] = uploader_phone
    if uploader_email:
        row["uploader_email"] = uploader_email
    try:
        _client().table("notes").insert(row).execute()
        return row
    except Exception as exc:
        logger.error("create_note error %s: %s", note_code, exc)
        return {}


def get_note(note_code: str) -> dict:
    """Fetch a single note row by code. Returns {} if not found."""
    try:
        result = _client().table("notes").select("*").eq("note_code", note_code).execute()
        return result.data[0] if result.data else {}
    except Exception as exc:
        logger.error("get_note error %s: %s", note_code, exc)
        return {}


def list_notes(
    category: str | None = None,
    status: str = "approved",
    limit: int = 50,
) -> list:
    """List notes ordered by print_count desc. Optionally filter by category."""
    try:
        q = _client().table("notes").select("*").eq("status", status)
        if category:
            q = q.eq("category", category)
        result = q.order("print_count", desc=True).limit(limit).execute()
        return result.data or []
    except Exception as exc:
        logger.error("list_notes error: %s", exc)
        return []


def update_note(note_code: str, **fields) -> bool:
    """Update arbitrary fields on a note row."""
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        _client().table("notes").update(fields).eq("note_code", note_code).execute()
        return True
    except Exception as exc:
        logger.error("update_note error %s: %s", note_code, exc)
        return False


def publish_note(note_code: str) -> bool:
    """Approve a note (admin action)."""
    return update_note(note_code, status="approved")


def reject_note(note_code: str, reason: str) -> bool:
    """Reject a note with a reason (admin action)."""
    return update_note(note_code, status="rejected", reject_reason=reason)


def upload_note_pdf(note_code: str, content: bytes) -> str:
    """Upload PDF to the private notes bucket. Returns storage path (not a URL)."""
    path = f"notes/{note_code}.pdf"
    try:
        _client().storage.from_(NOTES_BUCKET).upload(
            path=path,
            file=content,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )
        return path
    except Exception as exc:
        logger.error("upload_note_pdf error %s: %s", note_code, exc)
        return ""


def note_signed_url(storage_path: str, ttl: int = 3600) -> str:
    """Return a short-lived signed download URL for a private notes PDF."""
    try:
        result = _client().storage.from_(NOTES_BUCKET).create_signed_url(storage_path, ttl)
        return result.get("signedURL") or result.get("signed_url") or ""
    except Exception as exc:
        logger.error("note_signed_url error %s: %s", storage_path, exc)
        return ""


def pending_notes_queue(limit: int = 50) -> list:
    """All pending notes ordered by submission time — for admin moderation."""
    try:
        result = (
            _client().table("notes")
            .select("*")
            .eq("status", "pending")
            .order("created_at")
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        logger.error("pending_notes_queue error: %s", exc)
        return []


# ── credit wallet ─────────────────────────────────────────────────────────────

def wallet_balance(phone: str) -> int:
    """Return wallet balance in paise. Returns 0 if no wallet row exists."""
    try:
        result = (
            _client().table("credit_wallet")
            .select("balance_paise")
            .eq("phone", phone)
            .execute()
        )
        return result.data[0]["balance_paise"] if result.data else 0
    except Exception as exc:
        logger.error("wallet_balance error %s: %s", phone, exc)
        return 0


def wallet_add_credit(
    note_code: str,
    uploader_phone: str,
    print_job_id: str,
    pages: int,
    credit_paise: int,
) -> bool:
    """Credit the uploader's wallet after a notes print payment is confirmed.

    The caller (payment webhook) must gate this with _mark_webhook_processed
    to ensure it fires exactly once per payment event.
    """
    try:
        _client().table("note_credits").insert({
            "note_code": note_code,
            "uploader_phone": uploader_phone,
            "print_job_id": print_job_id,
            "pages_printed": pages,
            "credit_paise": credit_paise,
            "note": "print_commission",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        existing = wallet_balance(uploader_phone)
        _client().table("credit_wallet").upsert(
            {
                "phone": uploader_phone,
                "balance_paise": existing + credit_paise,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="phone",
        ).execute()
        return True
    except Exception as exc:
        logger.error("wallet_add_credit error %s: %s", uploader_phone, exc)
        return False


def wallet_redeem(phone: str, paise: int) -> bool:
    """Deduct `paise` from wallet. Returns False if insufficient balance.

    Uses an optimistic-lock conditional update to prevent double-spend.
    DB-level CHECK (balance_paise >= 0) is the final backstop.
    """
    try:
        result = (
            _client().table("credit_wallet")
            .select("balance_paise")
            .eq("phone", phone)
            .execute()
        )
        if not result.data:
            return False
        current = result.data[0]["balance_paise"]
        if current < paise:
            return False
        update_result = (
            _client().table("credit_wallet")
            .update({
                "balance_paise": current - paise,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("phone", phone)
            .eq("balance_paise", current)   # optimistic lock
            .execute()
        )
        if not update_result.data:
            logger.warning("wallet_redeem race condition for %s — caller should retry", phone)
            return False
        _client().table("note_credits").insert({
            "note_code": None,
            "uploader_phone": phone,
            "print_job_id": None,
            "pages_printed": 0,
            "credit_paise": -paise,
            "note": "redemption",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        return True
    except Exception as exc:
        logger.error("wallet_redeem error %s: %s", phone, exc)
        return False


# ── WhatsApp OTP sessions ─────────────────────────────────────────────────────

def create_otp_session(phone: str, request_token: str) -> bool:
    """Create a pending OTP session. Returns True on success."""
    try:
        _client().table("otp_sessions").insert({
            "phone": phone,
            "request_token": request_token,
            "status": "pending",
        }).execute()
        return True
    except Exception as exc:
        logger.error("create_otp_session error %s: %s", phone, exc)
        return False


def get_otp_session(request_token: str) -> dict:
    """Look up an OTP session by request_token."""
    try:
        result = (
            _client().table("otp_sessions")
            .select("*")
            .eq("request_token", request_token)
            .execute()
        )
        return result.data[0] if result.data else {}
    except Exception as exc:
        logger.error("get_otp_session error %s: %s", request_token, exc)
        return {}


def set_otp_code(request_token: str, otp_code: str) -> bool:
    """Set the OTP code on a pending session (called by bot after user messages)."""
    try:
        _client().table("otp_sessions").update({
            "otp_code": otp_code,
            "status": "sent",
        }).eq("request_token", request_token).eq("status", "pending").execute()
        return True
    except Exception as exc:
        logger.error("set_otp_code error %s: %s", request_token, exc)
        return False


def verify_otp_session(request_token: str, otp_code: str) -> dict:
    """Verify OTP. On success: marks verified, sets web_token, returns session dict."""
    import secrets as _secrets
    try:
        result = (
            _client().table("otp_sessions")
            .select("*")
            .eq("request_token", request_token)
            .eq("otp_code", otp_code)
            .eq("status", "sent")
            .execute()
        )
        if not result.data:
            return {}
        session = result.data[0]
        expires_at_str = session.get("expires_at", "")
        if expires_at_str:
            try:
                exp = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > exp:
                    _client().table("otp_sessions").update(
                        {"status": "expired"}
                    ).eq("id", session["id"]).execute()
                    return {}
            except Exception:
                pass
        web_token = _secrets.token_urlsafe(32)
        _client().table("otp_sessions").update({
            "status": "verified",
            "web_token": web_token,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", session["id"]).execute()
        session["web_token"] = web_token
        return session
    except Exception as exc:
        logger.error("verify_otp_session error %s: %s", request_token, exc)
        return {}


def get_otp_session_by_web_token(web_token: str) -> dict:
    """Look up a verified OTP session by its web_token (used for API auth)."""
    try:
        result = (
            _client().table("otp_sessions")
            .select("*")
            .eq("web_token", web_token)
            .eq("status", "verified")
            .execute()
        )
        return result.data[0] if result.data else {}
    except Exception as exc:
        logger.error("get_otp_session_by_web_token error: %s", exc)
        return {}


def note_subscription_status(phone: str) -> dict:
    """Return the active subscription row for a phone, or {} if not subscribed."""
    try:
        result = (
            _client().table("note_subscriptions")
            .select("*")
            .eq("phone", phone)
            .eq("status", "active")
            .execute()
        )
        return result.data[0] if result.data else {}
    except Exception as exc:
        logger.error("note_subscription_status error %s: %s", phone, exc)
        return {}


# ── Account identity linking (phone is the canonical customer id) ─────────────

def link_account_email(email: str, phone: str, name: str | None = None) -> bool:
    """Link a Supabase (Google/email) login to a canonical WhatsApp phone.

    Upsert on email so re-linking just updates the phone. Phone is what the
    wallet, credits, print jobs and subscriptions are all keyed on.
    """
    try:
        row: dict = {"email": (email or "").lower().strip(), "phone": phone}
        if name:
            row["name"] = name
        _client().table("account_links").upsert(row, on_conflict="email").execute()
        return True
    except Exception as exc:
        logger.error("link_account_email error %s: %s", email, exc)
        return False


def get_linked_phone(email: str) -> str | None:
    """Return the canonical phone linked to an email account, or None."""
    try:
        result = (
            _client().table("account_links")
            .select("phone")
            .eq("email", (email or "").lower().strip())
            .execute()
        )
        return result.data[0]["phone"] if result.data else None
    except Exception as exc:
        logger.error("get_linked_phone error %s: %s", email, exc)
        return None


def list_notes_by_uploader(phone: str) -> list:
    """All notes uploaded by a phone, any status, newest first.

    Unlike list_notes() (public catalogue, approved only), this returns the
    uploader's own notes including pending/rejected so they can see status.
    """
    try:
        result = (
            _client().table("notes")
            .select("*")
            .eq("uploader_phone", phone)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as exc:
        logger.error("list_notes_by_uploader error %s: %s", phone, exc)
        return []
