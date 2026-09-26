"""
timestamps.py — parsing the timestamp shapes this repo actually stores.

The same row reaches the reporting code from two places that do not agree on a
format: SQLite on the store PC writes "2026-08-17 14:03:00", Supabase returns
ISO-8601 with a zone and sometimes microseconds ("2026-08-17T14:03:00.123+00:00"
or "...Z"). Code that has to compare or age those values needs one permissive
parse rather than a format guess at each call site.

Deliberately permissive and deliberately naive: it drops the zone rather than
converting, because every caller compares against a local `datetime.now()` and
a half-converted mix would be worse than a consistent one. Returns None on
anything it cannot read, so callers can treat "no timestamp" and "unparseable
timestamp" the same way.
"""

from datetime import datetime

_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def as_datetime(value) -> datetime | None:
    """Parse a stored timestamp into a naive datetime, or None."""
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1]
    text = text.split("+")[0].split(".")[0].strip()
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
