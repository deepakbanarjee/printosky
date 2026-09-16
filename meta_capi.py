"""
META CONVERSIONS API — telling Meta which ad click turned into money.

WHY THIS MODULE EXISTS
----------------------
Ad 120248100283360186 ran 7-14 Sep 2026: 8 clicks, 7 people, 0 print jobs,
Rs.0. The campaign optimised for LINK_CLICKS, and the only signal Meta ever
received back was the click itself, so it went and bought more cheap clicks.
It was never once told that a click had paid, because the `ctwa_clid` that
SCHEMA v42 started storing went nowhere.

This is the return path. When an ad arrival pays, Meta is told, against the
click id it issued. That is the difference between buying clicks and buying
customers, and no ad copy substitutes for it.

THE REQUEST
-----------
    POST https://graph.facebook.com/v21.0/{DATASET_ID}/events
    Authorization: Bearer {token}

    {"data": [{
        "event_name":        "Purchase",
        "event_time":        <unix seconds>,
        "event_id":          "<order id>",          # Meta-side dedup
        "action_source":     "business_messaging",  # NOT "website"
        "messaging_channel": "whatsapp",
        "user_data":  {"ctwa_clid": "...", "page_id": "..."},
        "custom_data": {"currency": "INR", "value": 290.0}
    }]}

`action_source` and `messaging_channel` are the two fields that make this a
click-to-WhatsApp conversion rather than a website one. Without them Meta
accepts the event and attributes it to nothing, which is indistinguishable
from not sending it at all -- so they are asserted in the tests.

`ctwa_clid` is NOT hashed. Every other user identifier in the Conversions API
is SHA-256; this one is a click id Meta issued itself and it must arrive
verbatim.

CONFIGURATION
-------------
    META_CAPI_DATASET_ID   the dataset ("pixel") events are posted to.
                           Events Manager -> Data sources -> ... -> Dataset ID.
    META_CAPI_TOKEN        optional; falls back to META_SYSTEM_USER_TOKEN,
                           which is the token the WhatsApp sender already uses.
    META_PAGE_ID           optional; sent as user_data.page_id when set.

FAILING LOUD (docs/FAIL_LOUD.md)
--------------------------------
Three outcomes, three different noises, because they are three different
situations and only two of them are anyone's problem:

  * no ctwa_clid for this customer -- they did not come from an ad. Silent.
    This is the overwhelming majority of paying customers and it is not news.
  * a ctwa_clid we cannot send (unconfigured, HTTP error, Meta rejects it) --
    ALERT. We had an attributable conversion and dropped it, which is the
    exact failure v42 was written to end, one layer further out.
  * sent -- recorded, and a previously failing check announces its recovery.

Nothing here raises. A customer's payment must never fail because an ad
platform is down.
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com/v21.0"

# The check name ops_watchdog dedups on; also what the health console shows.
CHECK = "meta.capi"

# Meta rejects events whose event_time is more than seven days old. Nothing here
# guards against that because nothing here can hit it: report_purchase stamps
# the event at payment time, which is now. Anything that later drains the failed
# rows as a retry queue WILL need the guard -- a conversion that failed eight
# days ago cannot be re-sent with its original timestamp and should not be
# re-sent with a false one.

_HTTP_TIMEOUT = 10


def _dataset_id() -> str:
    return (os.environ.get("META_CAPI_DATASET_ID") or "").strip()


def _token() -> str:
    return (os.environ.get("META_CAPI_TOKEN")
            or os.environ.get("META_SYSTEM_USER_TOKEN")
            or "").strip()


def _page_id() -> str:
    return (os.environ.get("META_PAGE_ID") or "").strip()


def is_configured() -> bool:
    """Can we send at all? Both halves are required and neither has a default."""
    return bool(_dataset_id() and _token())


def _alert(ok: bool, detail: str) -> None:
    """Report to the watchdog. Never raises -- see docs/FAIL_LOUD.md."""
    try:
        from ops_watchdog import report
        report(CHECK, ok, detail)
    except Exception as exc:                      # pragma: no cover - defensive
        # A missing or broken watchdog must not swallow the reason itself.
        logger.error("meta_capi: watchdog unavailable (%s); %s: %s",
                     exc, "ok" if ok else "FAILED", detail)


def build_event(ctwa_clid: str, order_id: str, value_inr: float,
                event_name: str = "Purchase", currency: str = "INR",
                event_time: int | None = None) -> dict:
    """The event payload, as a pure function so the tests can read it.

    Split out from the send because the shape is the part that is easy to get
    quietly wrong: an event missing action_source/messaging_channel is accepted
    by Meta and attributed to nobody.
    """
    user_data: dict[str, object] = {"ctwa_clid": ctwa_clid}
    page_id = _page_id()
    if page_id:
        user_data["page_id"] = page_id

    event: dict[str, object] = {
        "event_name":        event_name,
        "event_time":        int(event_time if event_time is not None else time.time()),
        "event_id":          order_id,
        "action_source":     "business_messaging",
        "messaging_channel": "whatsapp",
        "user_data":         user_data,
    }
    if value_inr is not None:
        event["custom_data"] = {"currency": currency, "value": round(float(value_inr), 2)}
    return event


def send_event(ctwa_clid: str, order_id: str, value_inr: float,
               event_name: str = "Purchase",
               currency: str = "INR") -> tuple[bool, str, str]:
    """POST one event. Returns (ok, error, fbtrace_id). Never raises.

    The caller records all three, success or failure, so a conversion Meta
    never accepted is a row with the reason on it rather than a gap.
    """
    if not is_configured():
        return (False, "META_CAPI_DATASET_ID or token not set", "")

    payload = {"data": [build_event(ctwa_clid, order_id, value_inr,
                                    event_name=event_name, currency=currency)]}
    try:
        import requests
        resp = requests.post(
            f"{GRAPH_URL}/{_dataset_id()}/events",
            json=payload,
            headers={"Authorization": f"Bearer {_token()}"},
            timeout=_HTTP_TIMEOUT,
        )
        try:
            body = resp.json()
        except ValueError:
            body = {}
        fbtrace = str(body.get("fbtrace_id") or "")

        if resp.status_code != 200:
            err = (body.get("error") or {}).get("message") or resp.text[:300]
            return (False, f"HTTP {resp.status_code}: {err}", fbtrace)

        # A 200 with events_received == 0 is a rejection wearing a success's
        # clothes -- Meta took the request and kept none of it.
        received = body.get("events_received")
        if received is not None and int(received) < 1:
            return (False, f"accepted but events_received={received}", fbtrace)
        return (True, "", fbtrace)
    except Exception as exc:
        return (False, f"{type(exc).__name__}: {exc}", "")


def report_purchase(phone: str, order_id: str, value_inr: float) -> bool:
    """Tell Meta an ad click paid. Safe to call for EVERY paying customer.

    Returns True only when Meta accepted the event. False covers three very
    different things, which is why the noisy ones alert on the way past:
    the customer never clicked an ad (silent, and usual), we have a click id
    but cannot send it (alert), or this order was already reported (silent).

    Never raises: a payment must not fail because an ad platform is down.
    """
    phone = (phone or "").strip()
    order_id = (order_id or "").strip()
    if not phone or not order_id:
        return False

    try:
        from db_cloud import (ad_conversion_exists, ctwa_clid_for_phone,
                              record_ad_conversion)
    except Exception as exc:                      # pragma: no cover - defensive
        logger.error("meta_capi: db_cloud unavailable (%s)", exc)
        return False

    try:
        attribution = ctwa_clid_for_phone(phone)
    except Exception as exc:
        # We cannot tell whether this was an ad customer. Treat as failure:
        # staying quiet here is how an attributable conversion goes missing.
        _alert(False, f"attribution lookup failed for {order_id}: {exc}")
        return False

    if not attribution or not attribution.get("ctwa_clid"):
        return False                              # not an ad customer. Not news.

    ctwa_clid = attribution["ctwa_clid"]
    source_id = attribution.get("source_id") or ""

    try:
        if ad_conversion_exists(order_id):
            logger.info("meta_capi: %s already reported — skipping", order_id)
            return False
    except Exception as exc:
        # Better a possible duplicate (Meta dedups on event_id too) than a
        # conversion dropped because one read failed.
        logger.warning("meta_capi: dedup check failed for %s (%s) — sending anyway",
                       order_id, exc)

    if not is_configured():
        _alert(False,
               f"{order_id} is worth Rs.{value_inr} against ad {source_id or '?'} "
               f"and cannot be reported: META_CAPI_DATASET_ID / token not set")
        _record(record_ad_conversion, order_id, phone, ctwa_clid, source_id,
                value_inr, False, "not configured", "")
        return False

    ok, err, fbtrace = send_event(ctwa_clid, order_id, value_inr)
    _record(record_ad_conversion, order_id, phone, ctwa_clid, source_id,
            value_inr, ok, err, fbtrace)

    if ok:
        logger.info("meta_capi: reported %s (Rs.%s) against ad %s",
                    order_id, value_inr, source_id or "?")
        _alert(True, f"reported {order_id}")
    else:
        _alert(False, f"{order_id} (Rs.{value_inr}, ad {source_id or '?'}): {err}")
    return ok


def _record(recorder, order_id: str, phone: str, ctwa_clid: str, source_id: str,
            value_inr: float, ok: bool, error: str, fbtrace_id: str) -> None:
    """Persist the attempt. A failed write must not mask the send's own result."""
    try:
        recorder(order_id=order_id, phone=phone, ctwa_clid=ctwa_clid,
                 source_id=source_id, value_inr=value_inr, ok=ok,
                 error=error, fbtrace_id=fbtrace_id)
    except Exception as exc:
        logger.error("meta_capi: could not record conversion %s (ok=%s): %s",
                     order_id, ok, exc)
