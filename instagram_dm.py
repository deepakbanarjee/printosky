"""
INSTAGRAM DIRECT — receiving and answering DMs to the Printosky profile.

WHY THIS MODULE EXISTS
----------------------
Every other door into this shop is a phone number. Instagram Direct is not.
A person who DMs the profile is an IGSID — an Instagram-Scoped ID, unique to
that person AND that business account — and _normalize_phone() would turn one
into a phone number belonging to a stranger. So Instagram got no webhook, and
a DM produced no row, no console entry, no alert and no reply. Nobody finds
out until someone opens the app.

HOW MUCH TRAFFIC IS THIS
------------------------
Measured 2026-09-21: none. All 16 conversations Meta billed between 17 Aug and
21 Sep are WhatsApp; 14 of the 15 people in them have an ad_clicks row and the
15th first wrote two days before that table existed. The ad set's
destination_type is WHATSAPP, so an Instagram PLACEMENT of that ad still opens
WhatsApp — placement is where the ad is shown, not where the tap lands.

This is for organic DMs to the profile, and for the day an Instagram-Direct
ad runs. Built because the cost is asymmetric: an unanswered DM from someone
who found the shop on Instagram is a customer lost without a trace.

THE WEBHOOK
-----------
    {"object": "instagram",
     "entry": [{"id": "<ig account id>", "time": 1234567890,
                "messaging": [{"sender":    {"id": "<IGSID>"},
                               "recipient": {"id": "<ig account id>"},
                               "timestamp": 1234567890123,
                               "message":   {"mid": "...", "text": "hi"},
                               "referral":  {"ref": "...", "source": "ADS",
                                             "type": "OPEN_THREAD",
                                             "ad_id": "..."}}]}]}

Note `entry[].messaging[]`, NOT the `entry[].changes[].value.messages[]` shape
WhatsApp uses — the two products share a callback URL and a signature but not
a payload. Everything here is read defensively with .get(), because this shape
could not be checked against Meta's own documentation while it was written
(developers.facebook.com is unreachable from the build environment) and a
webhook that raises on an unexpected key would drop the message entirely.

An `is_echo` message is our OWN outbound coming back. Answering one is how a
bot ends up talking to itself forever, so they are dropped first.

CONFIGURATION
-------------
    INSTAGRAM_ACCOUNT_ID   the professional account's id; also the node
                           replies are POSTed to.
    INSTAGRAM_TOKEN        optional; falls back to META_SYSTEM_USER_TOKEN.

In the App Dashboard the app must be subscribed to BOTH `messages` and
`messaging_referrals` — without the second one an ad-originated DM arrives
with no ad on it, which is the attribution hole this whole line of work
exists to close.

FAILING LOUD (docs/FAIL_LOUD.md)
--------------------------------
A reply that does not send is reported, with Meta's own error message. The
send node is the one thing here that could not be verified against the docs,
so it is the one thing most likely to be wrong on the first live DM — and a
wrong node returns an HTTP error, which is visible, rather than a silent
success. `instagram.send` is the check to watch the first time this runs.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com/v21.0"
CHECK = "instagram.send"
_HTTP_TIMEOUT = 10


def account_id() -> str:
    return (os.environ.get("INSTAGRAM_ACCOUNT_ID") or "").strip()


def _token() -> str:
    return (os.environ.get("INSTAGRAM_TOKEN")
            or os.environ.get("META_SYSTEM_USER_TOKEN")
            or "").strip()


def is_configured() -> bool:
    """Can we reply at all? Receiving works without this; answering does not."""
    return bool(account_id() and _token())


# ── receiving ────────────────────────────────────────────────────────────────

def is_instagram_payload(data: dict) -> bool:
    """Does this webhook body belong to Instagram rather than WhatsApp?"""
    return str((data or {}).get("object") or "").strip().lower() == "instagram"


def parse_webhook(data: dict) -> list[dict]:
    """Flatten the payload into events. Never raises; unknown shapes yield [].

    Returns one dict per inbound message:
        {"igsid", "mid", "text", "timestamp", "referral", "attachments"}

    Echoes (our own outbound, `message.is_echo`) and read/delivery receipts are
    dropped here rather than by the caller, so there is exactly one place that
    decides what counts as a customer saying something.
    """
    events: list[dict] = []
    for entry in (data or {}).get("entry", []) or []:
        for item in (entry or {}).get("messaging", []) or []:
            message = (item or {}).get("message") or {}
            if message.get("is_echo"):
                continue                       # our own words coming back
            igsid = str(((item.get("sender") or {}).get("id") or "")).strip()
            if not igsid:
                continue
            referral = item.get("referral") or message.get("referral")
            has_content = bool(message.get("mid")) or bool(referral)
            if not has_content:
                continue                       # a read/delivery receipt
            events.append({
                "igsid":       igsid,
                "mid":         str(message.get("mid") or ""),
                "text":        (message.get("text") or "").strip(),
                "timestamp":   item.get("timestamp"),
                "referral":    referral or None,
                "attachments": message.get("attachments") or [],
            })
    return events


def referral_to_ad_click(referral: dict) -> dict:
    """Map Instagram's referral onto the shape db_cloud.record_ad_click stores.

    WhatsApp sends `source_id` / `ctwa_clid`; Instagram sends `ad_id` / `ref`
    and no click id at all. The names differ, the meaning of `source_id` does
    not — it is the ad — so it is normalised here and the ad report keeps
    working across both channels without knowing which is which.

    There is deliberately no ctwa_clid: Instagram does not issue one. That is
    why an Instagram conversion cannot be reported through meta_capi as it
    stands; see the note in _report_ad_conversion.
    """
    referral = referral or {}
    context = referral.get("ads_context_data") or {}
    return {
        "source_type": referral.get("source") or "ADS",
        "source_id":   str(referral.get("ad_id") or "") or None,
        "source_url":  referral.get("ref") or None,
        "ctwa_clid":   None,
        "headline":    context.get("ad_title") or None,
        "body":        referral.get("type") or None,
    }


# ── sending ──────────────────────────────────────────────────────────────────

_WA_MARKDOWN = re.compile(r"(?<!\w)([*_~])(?=\S)(.+?)(?<=\S)\1(?!\w)", re.S)


def strip_wa_markdown(text: str) -> str:
    """Instagram renders no markdown, so *bold* arrives with the asterisks.

    The shop's copy is written for WhatsApp and is full of them. Left alone,
    an Instagram customer reads "*Send your PDF right here*" and thinks the
    bot is broken before it has said anything else.
    """
    if not text:
        return ""
    prev = None
    out = text
    while prev != out:                      # *_nested_* needs more than one pass
        prev = out
        out = _WA_MARKDOWN.sub(r"\2", out)
    return out


def send_text(igsid: str, text: str) -> bool:
    """Reply in the DM thread. Returns True only when Meta accepted it.

    Never raises: an Instagram outage must not take the webhook down with it.
    A failure is reported rather than logged, because a reply that did not
    send is exactly as bad as never having written one.
    """
    igsid = (igsid or "").strip()
    body = strip_wa_markdown(text)
    if not igsid or not body:
        return False
    if not is_configured():
        _alert(False, "INSTAGRAM_ACCOUNT_ID / token not set — a DM went "
                      "unanswered because the app cannot reply on Instagram")
        return False

    try:
        import requests
        resp = requests.post(
            f"{GRAPH_URL}/{account_id()}/messages",
            json={"recipient": {"id": igsid}, "message": {"text": body}},
            headers={"Authorization": f"Bearer {_token()}"},
            timeout=_HTTP_TIMEOUT,
        )
        if resp.status_code == 200:
            _alert(True, "replying on Instagram")
            return True
        try:
            err = ((resp.json().get("error") or {}).get("message")
                   or resp.text[:300])
        except ValueError:
            err = resp.text[:300]
        _alert(False, f"HTTP {resp.status_code} replying to {igsid}: {err}")
        return False
    except Exception as exc:
        _alert(False, f"{type(exc).__name__} replying to {igsid}: {exc}")
        return False


def _alert(ok: bool, detail: str) -> None:
    """Report to the watchdog. Never raises — see docs/FAIL_LOUD.md."""
    try:
        from ops_watchdog import report
        report(CHECK, ok, detail)
    except Exception as exc:                  # pragma: no cover - defensive
        logger.error("instagram_dm: watchdog unavailable (%s); %s: %s",
                     exc, "ok" if ok else "FAILED", detail)
