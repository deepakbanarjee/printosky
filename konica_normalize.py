"""
KONICA FIELD NORMALISATION — one shape for `konica_jobs`, whoever wrote the row
==============================================================================

`konica_jobs` has had two writers, and they disagreed about the shape of three
columns. Nothing ever compared their output, so nothing ever noticed.

| column     | `konica_csv_importer` (Feb–Mar 2026) | `konica_jobs_fetcher` SOAP (Apr 2026 →) |
|------------|--------------------------------------|-----------------------------------------|
| `job_type` | `Print` / `Copy` / `Scan`            | `PRINT` / `COPY` / `SCAN`               |
| `result`   | `No Error` / `Canceled` / `Error`    | `OK` / `USERCANCEL` / `UNKNOWNERROR`    |
| `job_date` | `2026-03-16 09:46:14`                | `2026/09/02 09:18:59`                   |

What that cost, measured on production (2026-09-02, 14,864 rows):

* MIS filters `result=eq.No Error`, so from 2026-04-13 onward it matched **only
  the 1,980 rows the retired CSV importer wrote**. The Konica panel and the
  Staff Performance panel have been showing February–March data for five months.
* MIS also filters `job_date=gte.<today>` as a string. `/` (0x2F) sorts above
  `-` (0x2D), so every one of the 12,864 slash-dated rows passes *every* window
  filter — today, this week, this month, this year, all identical.
* `renderKJPeriod()` buckets on `job_type === "Print"` / `"Copy"`, so the
  12,864 upper-case rows counted as neither.

Three silent divergences, each individually plausible, together freezing a
panel on stale data while it kept rendering numbers. This module is the fix:
**one canonical shape, applied at write time, tolerated at read time.**

Unknown values are **kept, not dropped**. A value this module does not
recognise is a printer firmware change or a new job type, which is exactly the
thing worth an alert — never a silent discard.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

log = logging.getLogger("konica_normalize")

try:                                            # pragma: no cover - optional dep
    from ops_watchdog import report as _report
except Exception:                               # pragma: no cover
    def _report(*_a, **_kw):                    # type: ignore[misc]
        return None


# ── Canonical vocabularies ────────────────────────────────────────────────────
#: The three job types the machine produces, in the casing SCHEMA.sql documents.
JOB_TYPES: tuple[str, ...] = ("Print", "Copy", "Scan")

#: The job types that consume no paper. Their `pages_printed` is always 0 and a
#: page reconciliation has to read `num_pages` instead — see copy_reconciliation.
PAPERLESS_JOB_TYPES: frozenset[str] = frozenset({"Scan"})

#: The canonical "this job completed" result. Everything else is a failure or a
#: cancellation and must not be counted as work done.
RESULT_OK = "No Error"

#: SOAP result codes → the CSV vocabulary the consoles already read.
RESULT_ALIASES: dict[str, str] = {
    "OK":            RESULT_OK,
    "NOERROR":       RESULT_OK,
    "NO ERROR":      RESULT_OK,
    "USERCANCEL":    "Canceled",
    "CANCEL":        "Canceled",
    "CANCELED":      "Canceled",
    "CANCELLED":     "Canceled",
    "UNKNOWNERROR":  "Error",
    "ERROR":         "Error",
}

#: Paper sizes seen in production, upper-cased. `Legal` and `LEGAL` were two
#: buckets in the MIS breakdown; blank and NULL were two more.
_BLANK_SIZES = frozenset({"", "UNKNOWN", "NONE", "-"})


def normalize_job_type(raw) -> str | None:
    """`PRINT` / `print` / `Print` → `Print`. Unknown → alerted, kept verbatim.

    Returns None only for a genuinely empty value; an unrecognised non-empty
    value comes back unchanged so no row is ever silently reshaped into a type
    it is not.
    """
    text = ("" if raw is None else str(raw)).strip()
    if not text:
        return None
    for canonical in JOB_TYPES:
        if text.upper() == canonical.upper():
            return canonical
    _report("konica.job_type", False,
            f"konica_jobs.job_type={text!r} is not one of {', '.join(JOB_TYPES)} — "
            "the printer log has a job type this build does not know about, so it "
            "will not be counted in any print/copy breakdown.")
    return text


def normalize_result(raw) -> str | None:
    """`OK` → `No Error`, `USERCANCEL` → `Canceled`. Unknown → alerted, kept."""
    text = ("" if raw is None else str(raw)).strip()
    if not text:
        return None
    mapped = RESULT_ALIASES.get(text.upper())
    if mapped:
        return mapped
    _report("konica.result", False,
            f"konica_jobs.result={text!r} is a code this build does not map — "
            "jobs with it are excluded from every completed-work count until it "
            "is added to RESULT_ALIASES.")
    return text


#: The results this build understands. Anything else is a code the printer
#: produced and this module has never been taught — see is_known_result.
KNOWN_RESULTS: frozenset[str] = frozenset({RESULT_OK, "Canceled", "Error"})


def is_known_result(raw) -> bool:
    """Does this build understand what this result MEANS?

    The distinction that matters downstream: a `Canceled` job is known work
    that did not happen, and excluding it from a count is correct. An unmapped
    code is work of UNKNOWN status, and excluding it silently is a count
    pretending to a completeness it does not have — which is how a
    reconciliation quietly stops reconciling.

    Empty counts as known: a row with no result is missing data, not a code.
    """
    normalised = normalize_result(raw)
    return normalised is None or normalised in KNOWN_RESULTS


def is_ok(raw) -> bool:
    """True when this row represents work the machine actually completed.

    Tolerates either writer's vocabulary, so a console can call it on rows that
    have not been backfilled yet.
    """
    return normalize_result(raw) == RESULT_OK


_SLASH_DATE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?$")
_ISO_DATE   = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")


def normalize_job_date(raw) -> str | None:
    """`2026/09/02 09:18:59` → `2026-09-02 09:18:59`. ISO passes through.

    Returns None for anything unparseable rather than a guess: a wrong
    timestamp puts a job in the wrong day's revenue, which is worse than a job
    with no timestamp at all (the same rule store_digest.overdue_finishing
    follows).
    """
    text = ("" if raw is None else str(raw)).strip()
    if not text:
        return None

    m = _SLASH_DATE.match(text)
    if m:
        y, mo, d, h, mi, s = m.groups()
        return (f"{int(y):04d}-{int(mo):02d}-{int(d):02d} "
                f"{int(h):02d}:{int(mi):02d}:{int(s or 0):02d}")

    m = _ISO_DATE.match(text)
    if m:
        y, mo, d, h, mi, s = m.groups()
        if h is None:
            return f"{y}-{mo}-{d}"
        return f"{y}-{mo}-{d} {h}:{mi}:{int(s or 0):02d}"

    # The CSV shape, in case a legacy file is ever re-imported.
    for fmt in ("%d/%b/%Y %I:%M:%S %p", "%d/%b/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).isoformat(sep=" ", timespec="seconds")
        except ValueError:
            continue

    _report("konica.job_date", False,
            f"konica_jobs.job_date={text!r} is in no format this build parses — "
            "the row is kept but cannot be placed in a day, so it is missing from "
            "every dated report.")
    return None


def normalize_paper_size(raw) -> str | None:
    """`Legal`/`LEGAL` → `LEGAL`; blank, `-` and `UNKNOWN` → None.

    One bucket per size in the MIS breakdown, and one bucket — absent — for
    "the machine did not say", rather than three that look like three sizes.
    """
    text = ("" if raw is None else str(raw)).strip().upper()
    return None if text in _BLANK_SIZES else text


def normalize_row(row: dict) -> dict:
    """Apply every normaliser to a `konica_jobs`-shaped mapping.

    Returns a new dict; keys the row does not carry are left absent rather than
    invented, so this is safe on a partial `select`.
    """
    out = dict(row)
    for key, fn in (("job_type",   normalize_job_type),
                    ("result",     normalize_result),
                    ("job_date",   normalize_job_date),
                    ("paper_size", normalize_paper_size)):
        if key in out:
            out[key] = fn(out[key])
    return out


# ── The self-applying backfill ────────────────────────────────────────────────
#
# Store PCs update with PULL_UPDATE.bat + a watcher restart. Nothing runs
# fix_db.py for them, so a migration that needs to reach a counter has to apply
# itself on the path that needs it (CLAUDE.md). This one runs from the Konica
# fetcher, which is the only thing that writes the table.

#: Rows in one UPDATE pass. Kept small so a store PC's DB is never locked long.
BACKFILL_BATCH = 500


def backfill_sqlite(conn, *, batch: int = BACKFILL_BATCH) -> dict:
    """Rewrite legacy rows in a local `konica_jobs` into the canonical shape.

    Idempotent: a second run finds nothing to change. Returns a count per
    column so the caller can log what moved — and say nothing when nothing did.
    """
    changed = {"job_type": 0, "result": 0, "job_date": 0, "paper_size": 0}
    try:
        rows = conn.execute(
            "SELECT rowid, job_type, result, job_date, paper_size FROM konica_jobs"
        ).fetchall()
    except Exception as exc:
        _report("konica.backfill", False,
                f"could not read konica_jobs to normalise it: {type(exc).__name__}: {exc}")
        return changed

    pending: list[tuple] = []
    for rowid, job_type, result, job_date, paper_size in rows:
        new = (normalize_job_type(job_type), normalize_result(result),
               normalize_job_date(job_date), normalize_paper_size(paper_size))
        old = (job_type, result, job_date, paper_size)
        if new == old:
            continue
        for key, was, now in zip(changed, old, new):
            if was != now:
                changed[key] += 1
        pending.append((*new, rowid))

    for start in range(0, len(pending), batch):
        conn.executemany(
            "UPDATE konica_jobs SET job_type=?, result=?, job_date=?, paper_size=? "
            "WHERE rowid=?",
            pending[start:start + batch],
        )
        conn.commit()

    if pending:
        log.info("konica_jobs normalised: %d rows (%s)", len(pending),
                 ", ".join(f"{k} {v}" for k, v in changed.items() if v))
    return changed
