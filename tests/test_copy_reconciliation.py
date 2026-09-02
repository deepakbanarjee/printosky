"""
copy_reconciliation — the counter's word against the machine's.

Copying is the one job in this shop with an independent second witness: the
Konica logs it whether or not the sale was rung up. Everything here is about
making that comparison honest, including the cases where it must refuse to
produce a number.
"""

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import copy_reconciliation as cr

MIS = ROOT / "website" / "mis.html"


def M(job_type="COPY", pages=0, result="OK", **kw):
    """A konica_jobs row in the shape the live SOAP fetcher writes."""
    row = {"job_type": job_type, "result": result}
    row["num_pages" if job_type.upper() == "SCAN" else "pages_printed"] = pages
    row.update(kw)
    return row


def C_photocopy(sheets=1, copies=1):
    """A counter row from the B-6 /new-photocopy button — no service_kind."""
    return {"source": "Photocopy", "service_type": "Photocopy",
            "page_count": sheets, "copies": copies}


def C_service(kind="copy", sheets=1, copies=1):
    """A counter row from the B-3 /new-service path — service_kind + meta."""
    return {"service_kind": kind, "source": "Service",
            "service_meta": json.dumps({"sheets": sheets, "copies": copies})}


# ── Which counter rows count ──────────────────────────────────────────────────

def test_both_counter_paths_are_recognised():
    assert cr.counter_kind(C_photocopy()) == "Copy"
    assert cr.counter_kind(C_service("copy")) == "Copy"
    assert cr.counter_kind(C_service("scan")) == "Scan"


def test_a_photocopy_has_no_service_kind_and_is_still_counted():
    """B-6 deliberately gives a photocopy no service_kind so it stays inside the
    printer counts — which is what makes this reconciliation possible at all.
    Recognising it by source is therefore load-bearing, not a fallback."""
    row = C_photocopy(sheets=10, copies=2)
    assert row.get("service_kind") is None
    assert cr.counter_kind(row) == "Copy"
    assert cr.counter_pages(row) == 20


def test_a_row_matching_both_paths_is_one_job_not_two():
    row = {"service_kind": "copy", "source": "Photocopy",
           "service_type": "Photocopy", "page_count": 5, "copies": 1}
    totals = cr.counter_totals([row])
    assert totals["Copy"]["jobs"] == 1


def test_a_lamination_is_not_a_copy():
    for kind in ("laminate", "bind", "foil", "dtp", "photo"):
        assert cr.counter_kind({"service_kind": kind}) is None


def test_an_ordinary_print_job_is_not_a_copy():
    assert cr.counter_kind({"source": "WhatsApp", "service_type": "Print",
                            "page_count": 40, "printed_by": "priya"}) is None


# ── Counting pages ────────────────────────────────────────────────────────────

def test_counter_pages_is_sheets_times_copies():
    assert cr.counter_pages(C_photocopy(sheets=12, copies=3)) == 36
    assert cr.counter_pages(C_service("copy", sheets=12, copies=3)) == 36


def test_counter_pages_reads_meta_whether_it_is_json_text_or_an_object():
    as_text = {"service_kind": "copy", "service_meta": '{"sheets": 7, "copies": 2}'}
    as_obj  = {"service_kind": "copy", "service_meta": {"sheets": 7, "copies": 2}}
    assert cr.counter_pages(as_text) == cr.counter_pages(as_obj) == 14


def test_broken_meta_falls_back_to_the_columns_rather_than_raising():
    row = {"service_kind": "copy", "service_meta": "{not json",
           "page_count": 4, "copies": 2}
    assert cr.counter_pages(row) == 8


def test_a_missing_copies_count_means_one_not_zero():
    assert cr.counter_pages({"service_kind": "copy", "page_count": 9}) == 9


def test_a_scan_is_measured_by_num_pages_because_it_prints_nothing():
    """All 868 scan rows in production have pages_printed = 0. Reading it would
    report every scan as zero work."""
    scan = {"job_type": "SCAN", "result": "OK", "pages_printed": 0, "num_pages": 300}
    assert cr.machine_pages(scan) == 300


def test_a_copy_is_measured_by_pages_printed():
    assert cr.machine_pages(M("COPY", pages=250)) == 250


def test_a_copy_with_no_pages_printed_falls_back_to_num_pages():
    assert cr.machine_pages({"job_type": "COPY", "pages_printed": 0, "num_pages": 8}) == 8


# ── Which machine rows count ──────────────────────────────────────────────────

def test_print_jobs_are_excluded():
    """A print job arrives through the queue and is already accounted for by
    jobs.printed_by. Counting it here would invent a gap the size of the shop."""
    totals = cr.machine_totals([M("PRINT", pages=9999)])
    assert totals == {"Copy": {"jobs": 0, "pages": 0}, "Scan": {"jobs": 0, "pages": 0}}


def test_a_cancelled_copy_consumed_no_paper_and_owes_no_money():
    for result in ("USERCANCEL", "Canceled", "UNKNOWNERROR", "Error"):
        totals = cr.machine_totals([M("COPY", pages=500, result=result)])
        assert totals["Copy"]["pages"] == 0, result


def test_both_writers_row_shapes_are_counted_together():
    """The retired CSV importer's `Copy`/`No Error` and the live fetcher's
    `COPY`/`OK` are the same job type — that they were not is the whole reason
    konica_normalize exists."""
    totals = cr.machine_totals([
        M("COPY", pages=10, result="OK"),
        {"job_type": "Copy", "result": "No Error", "pages_printed": 10},
    ])
    assert totals["Copy"] == {"jobs": 2, "pages": 20}


def test_a_row_with_no_result_column_is_counted():
    """A partial `select` that omits `result` must not silently drop every row."""
    totals = cr.machine_totals([{"job_type": "COPY", "pages_printed": 40}])
    assert totals["Copy"]["pages"] == 40


# ── The verdict ───────────────────────────────────────────────────────────────

def test_a_window_with_no_machine_data_is_blind_never_zero_unbilled():
    """If the fetcher is down, the honest answer is "no machine data". Saying
    "0 unbilled" would be the panel lying in the reassuring direction."""
    r = cr.reconcile([], [C_photocopy(sheets=5)])
    assert r["status"] == "blind"
    assert r["types"]["Copy"]["status"] == "blind"


def test_blind_is_the_one_clean_state_that_still_speaks():
    assert "No Konica job log" in cr.format_reconciliation(cr.reconcile([], []))


def test_a_tiny_window_is_quiet_not_a_gap():
    r = cr.reconcile([M("COPY", pages=cr.GAP_FLOOR_PAGES - 1)], [])
    assert r["types"]["Copy"]["status"] == "quiet"
    assert cr.format_reconciliation(r) == ""


def test_the_floor_is_inclusive_at_the_boundary():
    at = cr.reconcile([M("COPY", pages=cr.GAP_FLOOR_PAGES)], [])
    assert at["types"]["Copy"]["status"] == "gap"


def test_a_fully_billed_window_is_ok_and_silent():
    r = cr.reconcile([M("COPY", pages=200)], [C_service("copy", sheets=200)])
    assert r["types"]["Copy"]["status"] == "ok"
    assert cr.format_reconciliation(r) == ""


def test_billing_more_than_the_machine_logged_is_not_a_fault():
    """Staff can bill a copy the machine logged just outside this window. That
    is a negative gap, reported as ok — not an error and not a gap."""
    r = cr.reconcile([M("COPY", pages=100)], [C_service("copy", sheets=150)])
    assert r["types"]["Copy"]["gap_pages"] == -50
    assert r["types"]["Copy"]["status"] == "ok"
    assert cr.format_reconciliation(r) == ""


def test_the_gap_threshold_is_a_share_not_a_count():
    just_under = cr.reconcile(
        [M("COPY", pages=1000)],
        [C_service("copy", sheets=int(1000 * (1 - cr.GAP_ALERT_FRACTION)) + 1)])
    assert just_under["types"]["Copy"]["status"] == "ok"
    at_the_line = cr.reconcile(
        [M("COPY", pages=1000)],
        [C_service("copy", sheets=int(1000 * (1 - cr.GAP_ALERT_FRACTION)))])
    assert at_the_line["types"]["Copy"]["status"] == "gap"


def test_production_shaped_data_reports_the_gap_it_actually_has():
    """The numbers this panel was built to surface: since 2026-04-13 the machine
    logged 3,640 copy jobs and 19,837 pages; the counter has recorded two
    photocopy sales, two pages, ever."""
    machine = [M("COPY", pages=19837), M("SCAN", pages=7612)]
    counter = [C_photocopy(sheets=1), C_photocopy(sheets=1)]
    r = cr.reconcile(machine, counter)
    assert r["status"] == "gap"
    assert r["gap_pages"] == 19837 + 7612 - 2
    assert r["gap_fraction"] > 0.99
    text = cr.format_reconciliation(r, "since April")
    assert "Unbilled copying since April" in text
    assert "Copy:" in text and "Scan:" in text


def test_one_type_can_be_fine_while_the_other_is_not():
    r = cr.reconcile([M("COPY", pages=200), M("SCAN", pages=500)],
                     [C_service("copy", sheets=200)])
    assert r["types"]["Copy"]["status"] == "ok"
    assert r["types"]["Scan"]["status"] == "gap"
    assert r["status"] == "gap"
    text = cr.format_reconciliation(r)
    assert "Scan:" in text and "Copy:" not in text


def test_an_all_quiet_window_never_outranks_a_real_reading():
    r = cr.reconcile([M("COPY", pages=200), M("SCAN", pages=2)],
                     [C_service("copy", sheets=200)])
    assert r["types"]["Scan"]["status"] == "quiet"
    assert r["status"] == "ok"


def test_nothing_anywhere_is_blind_not_ok():
    r = cr.reconcile([], [])
    assert r["status"] == "blind"


def test_reconcile_never_raises_on_junk_rows():
    junk = [{}, {"job_type": None}, {"job_type": "COPY", "pages_printed": "abc"},
            {"job_type": "COPY", "pages_printed": -5}]
    r = cr.reconcile(junk, [{}, {"service_kind": None}, {"service_kind": "copy",
                                                         "page_count": "x"}])
    assert r["types"]["Copy"]["machine_pages"] == 0


# ── The console computes the same number ──────────────────────────────────────

def _mis() -> str:
    return MIS.read_text(encoding="utf-8")


@pytest.mark.parametrize("name,value", [
    ("GAP_FLOOR_PAGES",    cr.GAP_FLOOR_PAGES),
    ("GAP_ALERT_FRACTION", cr.GAP_ALERT_FRACTION),
])
def test_the_console_constants_match_the_python(name, value):
    """Two implementations of one number is how they drift. If this fails,
    change both — the panel and the digest must agree."""
    m = re.search(rf"const {name}\s*=\s*([0-9.]+)", _mis())
    assert m, f"{name} is not defined in mis.html"
    assert float(m.group(1)) == float(value)


def test_the_console_reconciles_the_same_two_types():
    m = re.search(r"const RC_TYPES\s*=\s*\[(.*?)\]", _mis())
    assert m
    assert [t.strip().strip('"\'') for t in m.group(1).split(",")] == list(cr.RECONCILED_TYPES)


def test_the_console_knows_both_counter_paths():
    mis = _mis()
    assert "RC_COUNTER_KINDS" in mis and "photocopy" in mis
    for kind in cr.COUNTER_KINDS:
        assert kind in mis


def test_the_console_never_gates_the_counter_fetch_on_printed_by():
    """`printed_by=not.is.null` is what keeps service jobs out of the print
    panels. Applied here it would hide the very rows being counted."""
    mis = _mis()
    counter_fetch = re.search(r'sbFetch\("jobs", `received_at=gte\.\$\{dToday\}'
                              r'&select=\$\{RC_SELECT\}[^`]*`', mis)
    assert counter_fetch, "the counter-side jobs fetch is missing"
    assert "printed_by" not in counter_fetch.group(0)


def test_the_console_reads_num_pages_so_scans_are_not_reported_as_zero():
    assert "num_pages" in re.search(r'const KJ_SELECT = "(.*?)"', _mis(), re.S).group(1)


def test_the_console_no_longer_filters_on_the_retired_result_vocabulary():
    """`result=eq.No Error` in the query is the five-month bug. The filter now
    lives in kjOk(), which understands both writers."""
    mis = _mis()
    queries = re.findall(r'sbFetch\("konica_jobs", `([^`]*)`', mis)
    assert queries, "the konica_jobs fetches are missing"
    assert not any("result=eq" in q for q in queries), queries
    assert "function kjOk(" in mis
