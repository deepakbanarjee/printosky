"""Store-PC lifecycle digests — opening / closing messages with day, week and
month logs.

Pure, stdlib-only composition so it can be imported anywhere (the Vercel cron in
api/index.py composes these from `daily_summary` rows and sends them via
whatsapp_notify.send_staff_alert).

Working week is Mon-Sat; Sunday is closed. Therefore:
- last working day of the WEEK  = Saturday
- last working day of the MONTH = the last non-Sunday date of the month

A "summary" is a dict shaped like a `daily_summary` row:
    {"date": "YYYY-MM-DD", "total_jobs": int, "completed": int,
     "pending": int, "revenue": num, "cash": num, "upi": num}
"""
from __future__ import annotations

import calendar
from datetime import date, datetime
from typing import Iterable, Mapping, Sequence

# Mon=0 .. Sun=6. Oxygen works Mon-Sat.
WORKING_WEEKDAYS = frozenset({0, 1, 2, 3, 4, 5})


# ── Working-day logic ─────────────────────────────────────────────────────────
def is_working_day(d: date, working: frozenset = WORKING_WEEKDAYS) -> bool:
    return d.weekday() in working


def is_last_working_day_of_week(d: date, working: frozenset = WORKING_WEEKDAYS) -> bool:
    """True if `d` is the last working weekday of its week (Saturday for Mon-Sat)."""
    return d.weekday() == max(working)


def is_last_working_day_of_month(d: date, working: frozenset = WORKING_WEEKDAYS) -> bool:
    """True if `d` is a working day and no later day in the month is a working day."""
    if d.weekday() not in working:
        return False
    last_dom = calendar.monthrange(d.year, d.month)[1]
    for day in range(d.day + 1, last_dom + 1):
        if date(d.year, d.month, day).weekday() in working:
            return False
    return True


# ── Formatting helpers ────────────────────────────────────────────────────────
def _num(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _money(x) -> str:
    return f"₹{_num(x):,.0f}"


def _aggregate(rows: Iterable[Mapping]) -> dict:
    rows = list(rows or [])
    agg = {k: 0.0 for k in ("total_jobs", "completed", "pending", "revenue", "cash", "upi")}
    for r in rows:
        for k in agg:
            agg[k] += _num(r.get(k))
    dates = sorted(str(r.get("date")) for r in rows if r.get("date"))
    agg["start"] = dates[0] if dates else None
    agg["end"] = dates[-1] if dates else None
    agg["days"] = len(rows)
    return agg


def _as_date(d) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


# ── Summary aggregation from raw job rows ─────────────────────────────────────
# daily_summary is written by the store PC from its LOCAL SQLite, so it misses
# every job created directly in the cloud (the order API) and it split cash/upi
# with a case-sensitive payment_mode match — real data has cash/Cash/CASH — so
# the owner's day/week/month revenue logs read ~₹0. Aggregating the cloud `jobs`
# table (the source of truth) here fixes both, for jobs of any origin.

# Statuses that count as an open ("pending") job in the day log — everything
# that isn't Completed or Cancelled. Matched case-insensitively.
_PENDING_STATUSES = frozenset({
    "received", "in progress", "pending", "paid", "queued", "ready", "printed",
})


def summarize_jobs(rows: Iterable[Mapping], store_id=None) -> dict:
    """Aggregate raw `jobs` rows into per-date, `daily_summary`-shaped dicts.

    Each row is a mapping carrying at least ``received_at`` (a
    'YYYY-MM-DD[ /T]HH:MM:SS' string), ``status``, ``payment_mode`` and
    ``amount_collected``; any other keys are ignored. Rows are bucketed by
    DATE(received_at) and payment_mode is matched case-insensitively.

    Returns ``{date_str: summary_dict}`` where each summary has the same shape
    the day/week/month formatters expect: total_jobs, completed, pending,
    revenue, cash, upi (+ store_id, date).
    """
    by_date: dict = {}
    for r in rows or []:
        ra = str(r.get("received_at") or "")[:10]
        if len(ra) != 10:
            continue
        agg = by_date.get(ra)
        if agg is None:
            agg = by_date[ra] = {
                "store_id": store_id, "date": ra,
                "total_jobs": 0, "completed": 0, "pending": 0,
                "revenue": 0.0, "cash": 0.0, "upi": 0.0,
            }
        agg["total_jobs"] += 1
        status = str(r.get("status") or "").strip().lower()
        if status == "completed":
            agg["completed"] += 1
        elif status in _PENDING_STATUSES:
            agg["pending"] += 1
        amt = _num(r.get("amount_collected"))
        agg["revenue"] += amt
        mode = str(r.get("payment_mode") or "").strip().lower()
        if mode == "cash":
            agg["cash"] += amt
        elif mode == "upi":
            agg["upi"] += amt
    return by_date


# ── Log sections ──────────────────────────────────────────────────────────────
def format_daily_log(summary: Mapping) -> str:
    d = _as_date(summary.get("date"))
    return (
        f"\U0001f9fe Day log · {d:%a %d %b %Y}\n"
        f"• Jobs: {int(_num(summary.get('total_jobs')))} "
        f"(done {int(_num(summary.get('completed')))}, "
        f"pending {int(_num(summary.get('pending')))})\n"
        f"• Revenue: {_money(summary.get('revenue'))} "
        f"(cash {_money(summary.get('cash'))} · upi {_money(summary.get('upi'))})"
    )


def format_weekly_log(rows: Sequence[Mapping]) -> str:
    a = _aggregate(rows)
    span = f"{a['start']} → {a['end']}" if a["start"] else "this week"
    return (
        f"\U0001f4c5 Week summary · {span} ({a['days']} working days)\n"
        f"• Jobs: {int(a['total_jobs'])} | Revenue: {_money(a['revenue'])} "
        f"(cash {_money(a['cash'])} · upi {_money(a['upi'])})"
    )


def format_monthly_log(rows: Sequence[Mapping]) -> str:
    a = _aggregate(rows)
    label = "this month"
    if a["start"]:
        label = f"{_as_date(a['start']):%B %Y}"
    return (
        f"\U0001f5d3️ Month summary · {label} ({a['days']} working days)\n"
        f"• Jobs: {int(a['total_jobs'])} | Revenue: {_money(a['revenue'])} "
        f"(cash {_money(a['cash'])} · upi {_money(a['upi'])})"
    )


# ── Finishing sent to another store and not back (plan §4.12, B-8) ────────────
#
# A job at a finishing store is invisible: it is not in this shop's queue, not
# on this shop's printer, and the customer is still expecting it. Nattika went
# dark for a week in August 2026 because "locally reasonable" silences added up
# (see ops_watchdog), and a job sitting at the other shop is exactly that shape.

#: How long a transfer may sit before it earns a line in the digest.
FINISHING_OVERDUE_HOURS = 48


def overdue_finishing(rows: Iterable[Mapping], now: datetime | None = None,
                      hours: int = FINISHING_OVERDUE_HOURS) -> list[dict]:
    """Jobs sent out for finishing and not returned within `hours`.

    A row needs `job_id`, `finishing_store_id`, `finishing_status` and a
    `finishing_sent_at`-ish timestamp; rows missing any of it are skipped rather
    than guessed at, because a wrong age is worse than no line.
    """
    now = now or datetime.now()
    out: list[dict] = []
    for row in rows or []:
        status = str(row.get("finishing_status") or "").strip().lower()
        if status not in ("sent", "at_finisher"):
            continue
        sent = _as_datetime(row.get("finishing_sent_at") or row.get("received_at"))
        if sent is None:
            continue
        age = (now - sent).total_seconds() / 3600.0
        if age < hours:
            continue
        out.append({
            "job_id": row.get("job_id"),
            "store": row.get("finishing_store_id") or "?",
            "status": status,
            "hours": int(age),
        })
    return sorted(out, key=lambda r: -r["hours"])


def format_overdue_finishing(rows: Iterable[Mapping], now: datetime | None = None,
                             hours: int = FINISHING_OVERDUE_HOURS) -> str:
    """The digest section, or "" when there is nothing to say.

    Empty means silence on purpose: a daily "0 jobs overdue" line is the kind
    of green tick people stop reading, and this section only earns attention by
    being rare.
    """
    late = overdue_finishing(rows, now, hours)
    if not late:
        return ""
    head = (f"\u26a0\ufe0f {len(late)} job at a finishing store over {hours}h"
            if len(late) == 1 else
            f"\u26a0\ufe0f {len(late)} jobs at a finishing store over {hours}h")
    lines = [f"  {r['job_id']} — {r['store']}, {r['status']}, {r['hours']}h"
             for r in late]
    return "\n".join([head, *lines])


def _as_datetime(value) -> datetime | None:
    """Parse the timestamp shapes this repo actually stores, or None."""
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1]
    text = text.split("+")[0].split(".")[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


# ── Composed messages ─────────────────────────────────────────────────────────
def compose_opening_message(d: date | str) -> str:
    d = _as_date(d)
    return f"\U0001f7e2 Store PC online — opening {d:%A, %d %b %Y}. Good morning!"


def compose_closing_message(
    d: date | str,
    daily: Mapping,
    weekly_rows: Sequence[Mapping] | None = None,
    monthly_rows: Sequence[Mapping] | None = None,
    clean: bool = True,
    finishing_rows: Sequence[Mapping] | None = None,
) -> str:
    """Build the closing message.

    Always includes the day log. Appends the weekly log when `d` is the last
    working day of the week (Saturday) and `weekly_rows` is provided, and the
    monthly log when `d` is the last working day of the month and `monthly_rows`
    is provided. `clean=False` marks an unexpected offline (no clean shutdown).
    """
    d = _as_date(d)
    head = (
        f"\U0001f534 Store PC shutting down — closed {d:%A, %d %b %Y}"
        if clean else
        f"⚠️ Store PC went OFFLINE (no clean shutdown) — {d:%A, %d %b %Y}"
    )
    parts = [head, format_daily_log(daily)]
    # Only when there is something late — see format_overdue_finishing.
    overdue = format_overdue_finishing(finishing_rows or [])
    if overdue:
        parts.append(overdue)
    if weekly_rows is not None and is_last_working_day_of_week(d):
        parts.append(format_weekly_log(weekly_rows))
    if monthly_rows is not None and is_last_working_day_of_month(d):
        parts.append(format_monthly_log(monthly_rows))
    return "\n\n".join(parts)


# ── Up/down transition brain (pure; I/O lives in the cron handler) ─────────────
def decide_transition(
    *,
    online: bool,
    prev_state: str,
    today_str: str,
    close_date_str: str,
    opening_sent_date: str | None,
    closing_sent_date: str | None,
) -> dict:
    """Decide what the heartbeat cron should do this tick.

    - online + was down  -> send opening (once per `today_str`)
    - offline + was up    -> send closing (once per `close_date_str`)
    - first run (prev_state 'unknown') only records state, never alerts.

    Returns the actions plus the updated sent-date guards to persist.
    """
    send_opening = False
    send_closing = False
    opening_sent = opening_sent_date
    closing_sent = closing_sent_date

    if online:
        new_state = "up"
        if prev_state == "down" and opening_sent != today_str:
            send_opening = True
            opening_sent = today_str
    else:
        new_state = "down"
        if prev_state == "up" and closing_sent != close_date_str:
            send_closing = True
            closing_sent = close_date_str

    return {
        "new_state": new_state,
        "send_opening": send_opening,
        "send_closing": send_closing,
        "opening_sent_date": opening_sent,
        "closing_sent_date": closing_sent,
    }
