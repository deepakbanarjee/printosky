"""One clock for the whole system: the shop's clock.

`datetime.now()` means different things depending on which machine runs it.
On a store PC it is IST. On Vercel it is UTC. Printosky uses it for three
things — timestamps written to `jobs`, date-stamped identifiers customers are
quoted, and the range bounds MIS aggregates over — so a column, an order number
and a day's takings all came out different depending on which half of the system
happened to write them.

Found on 2026-09-06 with both halves visible in one screen: a counter job read
`13:22:32` and a web job `07:53:03`, thirty-one seconds apart in the shop and
five and a half hours apart in the column.

**The zone is Asia/Kolkata**, and it is the shop's local time on purpose:

* Store PCs already write IST and are most of the rows, so choosing UTC would
  mean converting the many to suit the few.
* Every reader — MIS, the digests, the REPL `report`, the console filters —
  already compares strings that are IST. Under UTC each of them needs a
  conversion, and the readers are the risky surface, not the writers.
* India has no DST and this is one chain in one timezone, so the usual argument
  for UTC does not apply.
* A job id should carry the **business** date. `OSKY-20260905` for a job taken
  at 02:00 IST is wrong to the person reading it; UTC makes that the norm.

A fixed +05:30 offset rather than `zoneinfo`: `zoneinfo` needs the `tzdata`
package present on Windows, and the store PCs are the one place this must never
fail to import. IST has no DST, so the offset is exact, not an approximation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: Asia/Kolkata. Fixed, because India does not observe DST.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

#: The shape every timestamp column in `jobs` uses. Space-separated, not 'T':
#: `api.index._sd_jobs_range()` bounds the column with plain string comparison,
#: so the format is load-bearing, not cosmetic.
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

#: The shape of the date inside a job id — `OSKY-20260906-0001`.
DATE_FORMAT = "%Y%m%d"

#: A compact date+time, used for storage object keys — and, through `ts[-4:]`,
#: for part of the WhatsApp job id, so it is customer-facing too.
STAMP_FORMAT = "%Y%m%d_%H%M%S"


def now() -> datetime:
    """The current moment, aware, in the shop's timezone."""
    return datetime.now(IST)


def now_str() -> str:
    """`'2026-09-06 13:22:32'` — what a timestamp column takes.

    Use this anywhere `datetime.now().strftime("%Y-%m-%d %H:%M:%S")` appears.
    The naive form is the bug: it reads the clock of whichever machine happens
    to run it.
    """
    return now().strftime(TIMESTAMP_FORMAT)


def today_str() -> str:
    """`'20260906'` — the date segment of a job id, in business time."""
    return now().strftime(DATE_FORMAT)


def stamp_str() -> str:
    """`'20260906_132232'` — for storage keys and id fragments."""
    return now().strftime(STAMP_FORMAT)


def to_ist(moment: datetime) -> datetime:
    """Move an existing datetime onto the shop's clock.

    A naive datetime is assumed to be UTC, which is the only assumption worth
    making: everything naive in this codebase that is *not* already IST came
    from a cloud container.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(IST)
