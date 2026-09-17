"""
printer_endpoints.py — where the diagnostic scripts get a printer address.

Why this module exists
----------------------
On 2026-06-29 the Epson EM-C8100 (192.168.55.214) replaced the WF-C21000
(192.168.55.202). The address was hardcoded in each Epson script, so every one
of them kept talking to a machine that was no longer there. The fix added a
config lookup — but added it by copy-paste, so four scripts each ended up with
their own identical resolver *and their own literal fallback IP*. The next
printer swap would have been the same outage with four places to remember.

This is that resolver, once. `EPSON_IP` in the environment wins (a technician
on site can override without editing anything), otherwise the store's own
`store_config.json` answers, and only if both are unavailable do we fall back to
the last-known OSP address — which is what the callers did before and is the
behaviour these scripts rely on when run on a box with no config file.
"""

import os

# Last-known Oxygen (OSP) address, used only when there is no env override and
# no readable store config. Kept deliberately as the single literal in the
# repo's diagnostic path: change it here or, better, in store_config.json.
_FALLBACK_EPSON_IP = "192.168.55.214"


def epson_ip() -> str:
    """This store's Epson IP: EPSON_IP env var, else store_config, else fallback."""
    override = os.environ.get("EPSON_IP")
    if override:
        return override.strip()
    try:
        from store_config import get_store_config
        return get_store_config().printers.epson_ip
    except Exception:
        # store_config is unreadable or absent (a bare box, a dev checkout).
        # The scripts are diagnostics — degrading to the known address keeps
        # them usable rather than making them crash on import.
        return _FALLBACK_EPSON_IP
