"""Referral-credit HTTP handlers — extracted from api/index.py.

Backs the /referrals/* endpoints (balance, redeem, leaderboard, credits).
Each is a plain handler taking the BaseHTTPRequestHandler instance `h`; the
router in api/index.py imports the entry points and dispatches to them.

Third slice of the api/index.py split. Shared helpers (incl. _acad_auth_staff,
also defined in api.index) are imported back from api.index.
"""
import json
import logging
from datetime import datetime
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("api.webhook")

from api.index import (  # noqa: E402  (api.index mid-import; names below defined above the import site)
    _json_response,
    _acad_auth_staff,
    _normalize_phone,
)

def _handle_referrals_balance(h) -> None:
    """GET /referrals/balance?phone=91XXXXXXXXXX — staff auth.
    Returns the customer's referral code and unredeemed store-credit balance.
    """
    if not _acad_auth_staff(h):
        _json_response(h, 401, {"error": "staff PIN required"})
        return
    qs = parse_qs(urlparse(h.path).query)
    phone = _normalize_phone(qs.get("phone", [""])[0] or "")
    if not phone:
        _json_response(h, 400, {"error": "phone parameter required"})
        return
    try:
        from db_cloud import _client
        sb = _client()
        ref = sb.table("referrers").select("code").eq("label", phone).execute()
        if not ref.data:
            _json_response(h, 200, {"phone": phone, "code": None, "balance": 0, "credits": []})
            return
        code = ref.data[0]["code"]
        rows = (sb.table("referral_credits")
                  .select("id,amount_inr,customer_phone,order_id,created_at")
                  .eq("referrer_code", code)
                  .is_("redeemed_at", "null")
                  .order("created_at")
                  .execute())
        credits = rows.data or []
        balance = sum(int(c.get("amount_inr") or 0) for c in credits)
        _json_response(h, 200, {
            "phone": phone, "code": code, "balance": balance, "credits": credits
        })
    except Exception as e:
        logger.error(f"_handle_referrals_balance error: {e}")
        _json_response(h, 500, {"error": "server error"})


def _handle_referrals_redeem(h, body: bytes) -> None:
    """POST /referrals/redeem — staff auth.
    Body: { phone, order_id, amount_inr, staff_id }
    Marks oldest unredeemed credits up to amount_inr as redeemed.

    Idempotent: if a redemption against (referrer_code, order_id) already exists,
    returns success without further changes.

    Race-safe: each row update filters on redeemed_at IS NULL — if another worker
    grabbed the same row first, the update returns 0 rows; we skip and try the next.
    """
    if not _acad_auth_staff(h):
        _json_response(h, 401, {"error": "staff PIN required"})
        return
    try:
        data = json.loads(body or b"{}")
    except Exception:
        _json_response(h, 400, {"error": "invalid JSON"})
        return
    phone    = _normalize_phone(data.get("phone") or "")
    order_id = (data.get("order_id") or "").strip()
    staff_id = (data.get("staff_id") or "").strip() or "unknown"
    try:
        amount = int(data.get("amount_inr") or 0)
    except Exception:
        amount = 0
    if not phone or not order_id or amount <= 0:
        _json_response(h, 400, {"error": "phone, order_id, amount_inr (>0) required"})
        return
    try:
        from db_cloud import _client
        sb = _client()
        ref = sb.table("referrers").select("code").eq("label", phone).execute()
        if not ref.data:
            _json_response(h, 404, {"error": "no referrer for this phone"})
            return
        code = ref.data[0]["code"]

        # Idempotency: same (code, redeemed_order_id) already booked? Return success.
        prior = (sb.table("referral_credits")
                   .select("id,amount_inr")
                   .eq("referrer_code", code)
                   .eq("redeemed_order_id", order_id)
                   .execute())
        if prior.data:
            already = sum(int(c.get("amount_inr") or 0) for c in prior.data)
            logger.info(f"Idempotent redeem: order {order_id} already had Rs.{already} from {code}")
            return _json_response(h, 200, {
                "ok": True, "redeemed": already,
                "applied_credit_ids": [c["id"] for c in prior.data],
                "idempotent": True,
            })

        rows = (sb.table("referral_credits")
                  .select("id,amount_inr")
                  .eq("referrer_code", code)
                  .is_("redeemed_at", "null")
                  .order("created_at")
                  .execute())
        available = rows.data or []
        total_available = sum(int(c.get("amount_inr") or 0) for c in available)
        if total_available < amount:
            _json_response(h, 400, {
                "error": "insufficient balance",
                "balance": total_available, "requested": amount
            })
            return

        applied: list[int] = []
        remaining = amount
        actually_redeemed = 0
        now_iso = datetime.utcnow().isoformat() + "Z"
        for row in available:
            if remaining <= 0:
                break
            credit_amt = int(row.get("amount_inr") or 0)
            if credit_amt <= remaining:
                # Atomic: only update if still unredeemed (race guard)
                upd = (sb.table("referral_credits").update({
                    "redeemed_at": now_iso,
                    "redeemed_order_id": order_id,
                    "redeemed_by": staff_id,
                }).eq("id", row["id"]).is_("redeemed_at", "null").execute())
                if upd.data:
                    applied.append(row["id"])
                    remaining -= credit_amt
                    actually_redeemed += credit_amt
                # else: another worker won the race; loop continues
            else:
                # Partial: shrink original row to `remaining` and mark redeemed,
                # insert leftover as new unredeemed row. Race-guard the shrink.
                upd = (sb.table("referral_credits").update({
                    "amount_inr": remaining,
                    "redeemed_at": now_iso,
                    "redeemed_order_id": order_id,
                    "redeemed_by": staff_id,
                }).eq("id", row["id"]).is_("redeemed_at", "null").execute())
                if upd.data:
                    sb.table("referral_credits").insert({
                        "referrer_code":  code,
                        "customer_phone": "split",
                        "order_id":       row.get("order_id") or order_id,
                        "amount_inr":     credit_amt - remaining,
                    }).execute()
                    applied.append(row["id"])
                    actually_redeemed += remaining
                    remaining = 0
        if actually_redeemed < amount:
            # Race lost on too many rows. Report what we got.
            logger.warning(f"Redeem partial: requested Rs.{amount}, got Rs.{actually_redeemed} from {code}")
            return _json_response(h, 409, {
                "error": "race lost — try again",
                "redeemed": actually_redeemed, "requested": amount,
                "applied_credit_ids": applied,
            })
        logger.info(f"Redeemed Rs.{actually_redeemed} from {code} for order {order_id} (staff {staff_id})")
        _json_response(h, 200, {
            "ok": True,
            "redeemed": actually_redeemed,
            "applied_credit_ids": applied,
            "remaining_balance": total_available - actually_redeemed,
        })
    except Exception as e:
        logger.error(f"_handle_referrals_redeem error: {e}")
        _json_response(h, 500, {"error": "server error"})


def _handle_referrals_leaderboard(h) -> None:
    """GET /referrals/leaderboard — staff auth.
    Returns all referrers with aggregated stats:
      [{code, label, platform, orders, earned_inr, redeemed_inr, balance_inr, created_at}]
    Sorted by balance_inr DESC.
    """
    if not _acad_auth_staff(h):
        _json_response(h, 401, {"error": "staff PIN required"})
        return
    try:
        from db_cloud import _client
        sb = _client()
        refs    = sb.table("referrers").select("code,label,platform,created_at").execute()
        credits = sb.table("referral_credits").select("referrer_code,amount_inr,redeemed_at").execute()

        # Aggregate in Python — small enough for now (P3 will need a Postgres view)
        agg: dict[str, dict] = {}
        for c in (credits.data or []):
            code = c.get("referrer_code")
            if not code:
                continue
            row = agg.setdefault(code, {"orders": 0, "earned": 0, "redeemed": 0})
            amt = int(c.get("amount_inr") or 0)
            row["orders"]  += 1
            row["earned"]  += amt
            if c.get("redeemed_at"):
                row["redeemed"] += amt

        out = []
        for r in (refs.data or []):
            code = r["code"]
            a = agg.get(code, {"orders": 0, "earned": 0, "redeemed": 0})
            out.append({
                "code":         code,
                "label":        r.get("label"),
                "platform":     r.get("platform"),
                "created_at":   r.get("created_at"),
                "orders":       a["orders"],
                "earned_inr":   a["earned"],
                "redeemed_inr": a["redeemed"],
                "balance_inr":  a["earned"] - a["redeemed"],
            })
        out.sort(key=lambda x: (x["balance_inr"], x["earned_inr"]), reverse=True)
        _json_response(h, 200, {"referrers": out})
    except Exception as e:
        logger.error(f"_handle_referrals_leaderboard error: {e}")
        _json_response(h, 500, {"error": "server error"})


def _handle_referrals_credits(h) -> None:
    """GET /referrals/credits?code=REFXXXX — staff auth.
    Returns the referrer's row plus every credit (redeemed and unredeemed),
    newest first. Used by the drill-in panel.
    """
    if not _acad_auth_staff(h):
        _json_response(h, 401, {"error": "staff PIN required"})
        return
    qs = parse_qs(urlparse(h.path).query)
    code = (qs.get("code", [""])[0] or "").strip().upper()
    if not code:
        _json_response(h, 400, {"error": "code parameter required"})
        return
    try:
        from db_cloud import _client
        sb = _client()
        ref = sb.table("referrers").select("code,label,platform,created_at").eq("code", code).execute()
        if not ref.data:
            _json_response(h, 404, {"error": "referrer not found"})
            return
        credits = (sb.table("referral_credits")
                     .select("id,customer_phone,order_id,amount_inr,created_at,"
                             "redeemed_at,redeemed_order_id,redeemed_by")
                     .eq("referrer_code", code)
                     .order("created_at", desc=True)
                     .execute())
        _json_response(h, 200, {
            "referrer": ref.data[0],
            "credits":  credits.data or [],
        })
    except Exception as e:
        logger.error(f"_handle_referrals_credits error: {e}")
        _json_response(h, 500, {"error": "server error"})
