"""
PRINTOSKY RAZORPAY INTEGRATION
================================
Creates Razorpay payment links for print jobs.
Verifies webhook signatures from Razorpay.

Credentials are loaded from .env (see .env.example).
"""

import hmac
import hashlib
import logging
import os
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("razorpay_integration")

# ── Credentials (loaded from .env) ────────────────────────────────────────────
RAZORPAY_KEY_ID     = os.environ["RAZORPAY_KEY_ID"]
RAZORPAY_KEY_SECRET = os.environ["RAZORPAY_KEY_SECRET"]
WEBHOOK_SECRET      = os.environ["RAZORPAY_WEBHOOK_SECRET"]  # must match Razorpay dashboard

BASE_URL = "https://api.razorpay.com/v1"

def _auth():
    return HTTPBasicAuth(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)

# ── Create payment link ───────────────────────────────────────────────────────
def create_payment_link(
    job_id: str,
    amount: float,
    description: str,
    customer_phone: str = None,
    expires_in_minutes: int = 60,
) -> dict:
    """
    Creates a Razorpay payment link for a print job.
    Returns {"url": ..., "link_id": ..., "short_url": ...} or {"error": ...}

    amount is in INR (rupees) — converted to paise internally.
    """
    import time
    paise = int(round(amount * 100))
    expire_by = int(time.time()) + (expires_in_minutes * 60)

    payload = {
        "amount":      paise,
        "currency":    "INR",
        "accept_partial": False,
        "description": description,
        "reference_id": job_id,          # our Job ID — returned in webhook
        "expire_by":   expire_by,
        "reminder_enable": True,
        "notify": {
            "sms":   bool(customer_phone),
            "email": False,
        },
        "notes": {
            "job_id": job_id,
            "store":  "Oxygen Globally, Thriprayar",
        },
        "callback_url":    f"https://printosky.com/payment-done?job={job_id}",
        "callback_method": "get",
    }

    if customer_phone:
        digits = "".join(c for c in customer_phone if c.isdigit())
        if len(digits) == 10:
            digits = "91" + digits
        payload["customer"] = {"contact": f"+{digits}"}

    try:
        r = requests.post(
            f"{BASE_URL}/payment_links",
            json=payload,
            auth=_auth(),
            timeout=10,
        )
        data = r.json()
        if r.status_code == 200:
            logger.info(f"Payment link created for {job_id}: {data.get('short_url')}")
            return {
                "url":      data.get("short_url") or data.get("short_url"),
                "link_id":  data.get("id"),
                "full_url": data.get("short_url"),
            }
        else:
            logger.error(f"Razorpay error: {data}")
            return {"error": data.get("error", {}).get("description", "Unknown error")}
    except Exception as e:
        logger.error(f"Razorpay request failed: {e}")
        return {"error": str(e)}

def create_academic_payment_link(
    project_id: str,
    payment_type: str,        # "advance" | "balance"
    amount: float,            # INR (rupees) — converted to paise internally
    description: str,
    customer_phone: str = None,
    customer_name: str = None,
    expires_in_minutes: int = 4320,   # 3 days — students may delay
) -> dict:
    """Razorpay payment link for academic orders.

    Notes are keyed off (project_id, payment_type) so the academic webhook
    handler at /academic/razorpay-webhook can match captures to orders and
    advance the right status (advance_paid / balance_paid).

    Returns {"url": ..., "link_id": ..., "short_url": ...} or {"error": ...}.
    """
    import time
    if payment_type not in ("advance", "balance"):
        return {"error": f"invalid payment_type: {payment_type!r}"}

    paise     = int(round(amount * 100))
    expire_by = int(time.time()) + (expires_in_minutes * 60)

    payload = {
        "amount":         paise,
        "currency":       "INR",
        "accept_partial": False,
        "description":    description,
        "reference_id":   f"{project_id}_{payment_type}",   # unique per project+phase
        "expire_by":      expire_by,
        "reminder_enable": True,
        "notify": {
            "sms":   bool(customer_phone),
            "email": False,
        },
        "notes": {
            "project_id":   project_id,
            "payment_type": payment_type,
            "store":        "Oxygen Students Paradise, Thrissur",
        },
        "callback_url":    f"https://printosky.com/payment-done?project={project_id}&phase={payment_type}",
        "callback_method": "get",
    }

    if customer_phone:
        digits = "".join(c for c in customer_phone if c.isdigit())
        if len(digits) == 10:
            digits = "91" + digits
        cust: dict = {"contact": f"+{digits}"}
        if customer_name:
            cust["name"] = str(customer_name)[:200]
        payload["customer"] = cust

    def _post(p: dict):
        return requests.post(f"{BASE_URL}/payment_links", json=p, auth=_auth(), timeout=10)

    try:
        r = _post(payload)
        data = r.json()
        # If Razorpay rejects the customer block (bad phone, recurring digits, etc.),
        # retry without it — the link still works, just no auto-SMS reminder.
        if r.status_code != 200 and "customer" in payload:
            err_desc = (data.get("error", {}) or {}).get("description", "").lower()
            if "contact" in err_desc or "customer" in err_desc or "phone" in err_desc:
                logger.warning(f"Razorpay rejected customer block for {project_id} ({err_desc}) — retrying without it")
                payload.pop("customer", None)
                payload["notify"]["sms"] = False
                r = _post(payload)
                data = r.json()
        if r.status_code == 200:
            logger.info(f"Academic payment link created for {project_id} ({payment_type}): {data.get('short_url')}")
            return {
                "url":      data.get("short_url"),
                "link_id":  data.get("id"),
                "short_url": data.get("short_url"),
            }
        logger.error(f"Razorpay academic link error for {project_id}: {data}")
        return {"error": (data.get("error") or {}).get("description", "Unknown error")}
    except Exception as e:
        logger.error(f"Razorpay academic link request failed for {project_id}: {e}")
        return {"error": str(e)}


# ── Verify webhook signature ──────────────────────────────────────────────────
def verify_webhook(payload_bytes: bytes, signature: str) -> bool:
    """
    Verify Razorpay webhook signature.
    payload_bytes = raw request body (bytes)
    signature = X-Razorpay-Signature header value
    """
    try:
        expected = hmac.new(
            WEBHOOK_SECRET.encode(),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception as e:
        logger.warning(f"Webhook verify error: {e}")
        return False

# ── Parse payment confirmed webhook ──────────────────────────────────────────
def parse_payment_webhook(data: dict) -> dict:
    """
    Parse Razorpay webhook payload.
    Returns {"job_id": ..., "amount": ..., "payment_id": ..., "status": ...}
    or None if not a payment success event.
    """
    event = data.get("event", "")
    if event not in ("payment_link.paid", "payment.captured"):
        return None

    try:
        if event == "payment_link.paid":
            pl     = data["payload"]["payment_link"]["entity"]
            pay    = data["payload"]["payment"]["entity"]
            job_id = pl.get("reference_id") or pl.get("notes", {}).get("job_id")
            amount = pay.get("amount", 0) / 100
            pay_id = pay.get("id")
            method = pay.get("method", "UPI")
        else:
            pay    = data["payload"]["payment"]["entity"]
            job_id = pay.get("notes", {}).get("job_id")
            amount = pay.get("amount", 0) / 100
            pay_id = pay.get("id")
            method = pay.get("method", "UPI")

        if not job_id:
            return None

        return {
            "job_id":     job_id,
            "amount":     amount,
            "payment_id": pay_id,
            "method":     method.upper(),
        }
    except Exception as e:
        logger.warning(f"parse_payment_webhook error: {e}")
        return None
