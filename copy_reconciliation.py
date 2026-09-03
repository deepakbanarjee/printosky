"""
COPY / SCAN RECONCILIATION — the counter's word against the machine's
=====================================================================

Every other number in this system is self-reported: a sale exists because
somebody filed it. Copying is the one job where a second, independent witness
already exists — the Konica writes its own log, whether or not anyone rang up
the sale (`konica_jobs.job_type` = `Copy` / `Scan`, SCHEMA.sql:210).

Comparing the two makes unbilled walk-in copying visible for the first time
(plan §4.11). What that comparison says on production data (2026-09-02):

    machine, since 2026-04-13:  3,640 copy jobs · 19,837 pages
                                  811 scan jobs ·  7,612 pages
    counter, ever:                  2 photocopy sales ·      2 pages

This module is deliberately pure — mappings in, a dict out, no I/O — so the
console, the digest and the tests all compute the identical number.

Two honesty rules it follows, because a reconciliation nobody trusts is worse
than none:

**Pages, not jobs, is the number.** One counter sale routinely covers several
machine jobs (a customer copies three documents, pays once), so a job-count gap
is noise. Pages are what the shop bills and what the machine counts.

**A window with no machine data reconciles to nothing, not to zero.** If the
fetcher is down, the honest answer is "no machine data", never "0 unbilled".
That distinction is `status == "blind"` and it alerts.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping

from konica_normalize import (
    PAPERLESS_JOB_TYPES,
    is_known_result,
    is_ok,
    normalize_job_type,
)

log = logging.getLogger("copy_reconciliation")

#: The machine job types this reconciles. `Print` is excluded — a print job
#: arrives through the queue and is already accounted for by `jobs.printed_by`.
RECONCILED_TYPES: tuple[str, ...] = ("Copy", "Scan")

#: `service_kind` values the counter writes for the same work (B-3 /new-service).
COUNTER_KINDS: dict[str, str] = {"copy": "Copy", "scan": "Scan"}

#: The pre-B-3 photocopy button (B-6 /new-photocopy) files a job with no
#: `service_kind` — deliberately, so it stays inside the printer counts and this
#: reconciliation works at all (pinned by test_a_photocopy_is_not_a_service_job).
#: It is identified by source/service_type instead.
COUNTER_PHOTOCOPY_MARKERS: frozenset[str] = frozenset({"photocopy"})

#: Below this many machine pages a window is too small to mean anything — a gap
#: of four pages is a staff test copy, not lost revenue.
GAP_FLOOR_PAGES = 25

#: At or above this share of machine pages unrecorded, the window is flagged.
#: 0.20 is deliberately loose: the point is to catch "almost nothing is being
#: rung up", which is what production actually shows, not to police rounding.
GAP_ALERT_FRACTION = 0.20


def _int(value) -> int:
    """A count from whatever the row holds, never raising and never negative."""
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, n)


def _meta(row: Mapping) -> dict:
    """`service_meta` as a dict, whether it arrives as jsonb or as text.

    SQLite stores the JSON string; Supabase stores a jsonb object (supabase_sync
    converts it). Both reach this function.
    """
    raw = row.get("service_meta")
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def counter_kind(row: Mapping) -> str | None:
    """`Copy`, `Scan` or None for a `jobs` row, from either counter path.

    Checked in order: the B-3 `service_kind`, then the B-6 photocopy markers.
    A row that matches both is one job, counted once.
    """
    kind = str(row.get("service_kind") or "").strip().lower()
    if kind in COUNTER_KINDS:
        return COUNTER_KINDS[kind]

    for field in ("source", "service_type"):
        if str(row.get(field) or "").strip().lower() in COUNTER_PHOTOCOPY_MARKERS:
            return "Copy"
    return None


def counter_pages(row: Mapping) -> int:
    """Pages this counter row says were copied or scanned.

    `/new-photocopy` puts the sheet count in `page_count` and the run length in
    `copies`; `/new-service` puts both inside `service_meta`. Either way the
    billed quantity is sheets x copies, which is what the machine counts too.
    """
    meta = _meta(row)
    sheets = _int(meta.get("sheets")) or _int(row.get("page_count"))
    copies = _int(meta.get("copies")) or _int(row.get("copies")) or 1
    return sheets * copies


def machine_pages(row: Mapping) -> int:
    """Pages this machine row represents.

    A scan prints nothing, so its `pages_printed` is always 0 and its size is in
    `num_pages` — verified across all 868 scan rows in production. Reading
    `pages_printed` for a scan would report every scan as zero work.
    """
    job_type = normalize_job_type(row.get("job_type"))
    if job_type in PAPERLESS_JOB_TYPES:
        return _int(row.get("num_pages"))
    return _int(row.get("pages_printed")) or _int(row.get("num_pages"))


def machine_totals(rows: Iterable[Mapping]) -> dict:
    """Per-type job and page counts from `konica_jobs` rows.

    Only completed rows count: a cancelled copy consumed no paper and owes no
    money. `is_ok` tolerates either writer's vocabulary, so this is correct on
    rows the backfill has not reached yet.

    Rows whose result this build does NOT understand are counted separately as
    `unknown`, not silently dropped. Discovered on 2026-09-02, when the printer
    turned out to emit a code (`X2`) nothing had mapped: excluding it the same
    way a cancellation is excluded would shrink the machine side of the
    comparison, and a gap that reads smaller than it is, is worse than no gap
    at all — this module exists to catch exactly that shape of quiet.
    """
    out = {t: {"jobs": 0, "pages": 0, "unknown_jobs": 0, "unknown_pages": 0}
           for t in RECONCILED_TYPES}
    for row in rows or []:
        job_type = normalize_job_type(row.get("job_type"))
        if job_type not in out:
            continue
        if "result" in row and not is_known_result(row.get("result")):
            out[job_type]["unknown_jobs"] += 1
            out[job_type]["unknown_pages"] += machine_pages(row)
            continue
        if "result" in row and not is_ok(row.get("result")):
            continue
        out[job_type]["jobs"] += 1
        out[job_type]["pages"] += machine_pages(row)
    return out


def counter_totals(rows: Iterable[Mapping]) -> dict:
    """Per-type job and page counts from `jobs` rows, both counter paths."""
    out = {t: {"jobs": 0, "pages": 0} for t in RECONCILED_TYPES}
    for row in rows or []:
        kind = counter_kind(row)
        if kind not in out:
            continue
        out[kind]["jobs"] += 1
        out[kind]["pages"] += counter_pages(row)
    return out


def reconcile(machine_rows: Iterable[Mapping],
              counter_rows: Iterable[Mapping]) -> dict:
    """The comparison, per type and in total.

    Each type carries a `status`:

    * `blind`   — the machine logged nothing, so there is nothing to compare.
                  Not the same as "nothing unbilled", and never reported as 0.
    * `quiet`   — fewer than GAP_FLOOR_PAGES machine pages; too small to judge.
    * `gap`     — at least GAP_ALERT_FRACTION of machine pages went unrecorded.
    * `ok`      — the counter accounts for the machine.

    A counter total *above* the machine's is not an error and not a gap: staff
    can bill a copy the machine logged in a window this one does not cover. It
    reports as `ok` with a negative gap rather than as a fault.
    """
    machine = machine_totals(machine_rows)
    counter = counter_totals(counter_rows)

    per_type: dict[str, dict] = {}
    for job_type in RECONCILED_TYPES:
        m, c = machine[job_type], counter[job_type]
        gap = m["pages"] - c["pages"]
        if m["pages"] == 0 and m["jobs"] == 0:
            status = "blind"
        elif m["pages"] < GAP_FLOOR_PAGES:
            status = "quiet"
        elif gap > 0 and (gap / m["pages"]) >= GAP_ALERT_FRACTION:
            status = "gap"
        else:
            status = "ok"
        per_type[job_type] = {
            "machine_jobs":   m["jobs"],
            "machine_pages":  m["pages"],
            "counter_jobs":   c["jobs"],
            "counter_pages":  c["pages"],
            "gap_pages":      gap,
            "gap_fraction":   (gap / m["pages"]) if m["pages"] else 0.0,
            "unknown_jobs":   m["unknown_jobs"],
            "unknown_pages":  m["unknown_pages"],
            "status":         status,
        }

    total_machine = sum(v["machine_pages"] for v in per_type.values())
    total_counter = sum(v["counter_pages"] for v in per_type.values())
    total_gap = total_machine - total_counter
    statuses = {v["status"] for v in per_type.values()}
    overall = ("gap"   if "gap" in statuses else
               "ok"    if "ok" in statuses else
               "quiet" if "quiet" in statuses else
               "blind")

    unknown_jobs = sum(v["unknown_jobs"] for v in per_type.values())
    unknown_pages = sum(v["unknown_pages"] for v in per_type.values())

    return {
        "types":         per_type,
        "machine_pages": total_machine,
        "counter_pages": total_counter,
        "gap_pages":     total_gap,
        "gap_fraction":  (total_gap / total_machine) if total_machine else 0.0,
        # Neither billed nor counted as machine work: the comparison could not
        # place these at all, and says so rather than rounding them away.
        "unknown_jobs":  unknown_jobs,
        "unknown_pages": unknown_pages,
        "status":        overall,
    }


def format_reconciliation(result: Mapping, window: str = "today") -> str:
    """The digest section, or "" when there is nothing worth saying.

    Silent on `ok` and on `quiet`, exactly like format_overdue_finishing: a
    daily "copies all accounted for" line is a green tick people stop reading.
    It speaks for a real gap, and it speaks for `blind`, because a reconciliation
    that has quietly stopped reconciling is the failure this whole module exists
    to prevent.
    """
    status = result.get("status")

    unknown = int(result.get("unknown_jobs") or 0)
    unknown_pages = int(result.get("unknown_pages") or 0)
    if unknown == 1:
        unknown_line = (f"\u26a0\ufe0f 1 machine job ({unknown_pages} pages) carries a "
                        "result code this build does not understand — it is in "
                        "neither column, so this comparison is incomplete by at "
                        "least that much.")
    elif unknown:
        unknown_line = (f"\u26a0\ufe0f {unknown} machine jobs ({unknown_pages} pages) carry "
                        "result codes this build does not understand — they are in "
                        "neither column, so this comparison is incomplete by at "
                        "least that much.")
    else:
        unknown_line = ""

    if status == "blind":
        if unknown_line:
            # NOT the same as no data. The printer sent rows; this build could
            # not read their result codes. Reporting "the fetcher has not
            # reached the printer" here would point the reader at the wrong
            # cause entirely — a misdiagnosis is worse than a bare unknown.
            return ("⚠️ Nothing could be classified for " + window +
                    " — copy/scan billing cannot be checked.\n" + unknown_line)
        return ("⚠️ No Konica job log for " + window +
                " — copy/scan billing cannot be checked. The job fetcher has not "
                "reached the printer.")

    if status != "gap":
        # An unclassifiable row is worth saying even when the rest reconciles:
        # the silence would otherwise be indistinguishable from agreement.
        return unknown_line

    lines = [f"⚠️ Unbilled copying {window}: "
             f"{result['gap_pages']} of {result['machine_pages']} machine pages "
             f"were never rung up ({result['gap_fraction'] * 100:.0f}%)"]
    for job_type in RECONCILED_TYPES:
        t = result["types"][job_type]
        if t["status"] != "gap":
            continue
        lines.append(f"  {job_type}: machine {t['machine_pages']} pages "
                     f"({t['machine_jobs']} jobs) · counter {t['counter_pages']} pages "
                     f"({t['counter_jobs']} jobs)")
    if unknown_line:
        lines.append(unknown_line)
    return "\n".join(lines)
