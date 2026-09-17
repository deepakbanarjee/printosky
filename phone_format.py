"""
phone_format.py — one canonical form for a customer's phone number.

A number reaches us as a WhatsApp JID ("919495706405@c.us", "...@lid",
"...@s.whatsapp.net"), as something a staff member typed at the counter, or as
a bare ten-digit mobile. Anything that keys a customer off a phone number —
sessions, referral credit, wallet balance, the conversation log — has to agree
on which of those strings are the same person, or the same customer silently
becomes two.

The canonical form is digits only, with 91 prepended to a bare ten-digit Indian
mobile: "919495706405".
"""

_JID_SUFFIXES = ("@c.us", "@lid", "@s.whatsapp.net")


def normalize_phone(p: str) -> str:
    """Canonicalize a phone/JID to digits-only Indian format. '' if empty."""
    if not p:
        return ""
    s = str(p)
    for suffix in _JID_SUFFIXES:
        s = s.replace(suffix, "")
    s = s.strip()
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) == 10:  # bare Indian mobile
        digits = "91" + digits
    return digits
