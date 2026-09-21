"""
The last resort: stop guessing and fetch a human.

WHY THIS MODULE EXISTS
----------------------
The handoff already existed, in api/index._handle_text. It flags the session,
acks the customer and alerts staff when nothing else understood the message. It
has never once fired for someone who arrived from an ad, because it is guarded
by `customer_is_idle` and sits BELOW the branch that, for an idle customer,
calls route_front_door and returns. Every ad arrival is idle by definition, so
the guard is only ever True on a path that has already returned — the net was
unreachable for exactly the people we pay Meta to send us.

Ad 120248100283360186, 18 Sep 2026, a Hindi speaker:

    bot:  [ad welcome]
    cust: "Aap ka photo"
    bot:  [English list menu]
    cust: "Kya hai ye"          <- "what is this"
          ... nothing, ever

`needs_human` stayed false. No staff alert. Nobody was told a paid lead was
lost, which is the fail-loud rule (docs/FAIL_LOUD.md) broken on the most
expensive conversation the shop has.

So the primitives move here, where both the front door (routing.intent) and the
print flow (api.index) can reach them without importing each other.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Short acknowledgements / option inputs that should NOT fetch a human.
_HANDOFF_NOOP = {
    "ok", "okay", "k", "kk", "thanks", "thank you", "thankyou", "ty", "tq",
    "done", "good", "great", "nice", "fine", "cool", "yes", "no", "y", "n",
    "👍", "🙏", "ശരി", "നന്ദി", "ഓക്കെ",
    # Greetings. "hi" was already excluded, but only by the length floor, so
    # "hello" and "namaskaram" were fetching a human to answer a hello. The
    # menu is the right answer to a greeting; a person is not.
    "hi", "hii", "hiii", "hey", "helo", "hlo", "hello", "hallo",
    "good morning", "good afternoon", "good evening", "gm", "gn",
    "namaskaram", "namaste", "vanakkam", "salam", "salaam",
    "assalamu alaikum", "assalamualaikum",
    "ഹലോ", "ഹായ്", "നമസ്കാരം",
}

_ACK = ("🙏 Thanks for your message — a team member will reply to you shortly. "
        "You can keep typing in the meantime.")


def fmt_phone(phone: str) -> str:
    """Format a phone number for display. 919495706405 → +91 94957 06405."""
    phone = (phone or "").strip()
    if len(phone) == 12 and phone.startswith("91"):
        return f"+91 {phone[2:7]} {phone[7:]}"
    return ("+" + phone) if not phone.startswith("+") else phone


def should_handoff_text(text: str) -> bool:
    """Heuristic: is this free-text worth routing to a human (vs ignoring)?

    True for real questions/sentences; False for courtesy words, bare digits/
    option taps, emoji, and very short inputs.
    """
    t = (text or "").strip()
    if len(t) < 3:
        return False
    if t.lower() in _HANDOFF_NOOP:
        return False
    # Needs a couple of letters (any script) — filters bare digits, punctuation
    # and emoji, while accepting Malayalam, where combining vowel marks would
    # break a consecutive-letter run.
    return sum(1 for ch in t if ch.isalpha()) >= 2


def mark_needs_human(phone: str) -> None:
    """Raise the SOS flag the Conversations console filters on."""
    from datetime import datetime, timezone
    from ops_watchdog import guard
    with guard("handoff.flag", f"could not flag {phone} as needing a human",
               reraise=False):
        from db_cloud import _client
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _client().table("bot_sessions").upsert({
            "phone": phone,
            "needs_human": True,
            "last_help_request_at": ts,
        }).execute()


def hand_off_to_human(phone: str, text: str, why: str,
                      *, hold: bool = True, ack: bool = True) -> None:
    """Flag the conversation, ack the customer, tell staff. Never raises.

    `hold` parks the session on `staff_hold` so the bot stays quiet on the
    follow-ups instead of answering over the person now reading the thread;
    it auto-clears once the session goes stale, and /staff/resume clears it
    by hand.

    The staff alert is the point of the whole function, so it is reported
    rather than swallowed: a handoff nobody hears about is the silence this
    module exists to end.
    """
    from ops_watchdog import guard
    from whatsapp_notify import _send, send_staff_alert

    mark_needs_human(phone)
    if hold:
        with guard("handoff.hold", f"could not park {phone} on staff_hold",
                   reraise=False):
            from db_cloud import save_session
            save_session("supabase", phone, step="staff_hold", needs_human=True)

    if ack:
        with guard("handoff.ack", f"could not ack {phone}", reraise=False):
            _send(phone, _ACK)

    with guard("handoff.alert", f"could not alert staff about {phone}",
               reraise=False):
        ok = send_staff_alert(
            f"🤖→🧑 {why} — {fmt_phone(phone)}: \"{(text or '').strip()[:80]}\". "
            f"Open Conversations → 'Needs human'."
        )
        if not ok:
            raise RuntimeError("send_staff_alert returned False")
