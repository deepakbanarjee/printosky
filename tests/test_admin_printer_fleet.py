"""
Static checks on the admin/jobs consoles' printer rules.

These pages have no JS test harness (tests/test_browser_admin.py drives the
live site), so the invariants that would otherwise rot silently are asserted
against the source:

  * Nattika (PRINTK) has no Konica in the shared fleet map, and the office box
    (PRIOFF) does not either — the consoles hide what a store does not own.
  * A printer is identified by model family, not by the word "epson": the
    dispatched queue name is "EM-C8100 Series(Network)".
  * Neither console still hardcodes the retired WF-C21000.
"""

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "website"
SHARED = (WEB / "admin-shared.js").read_text(encoding="utf-8")
CONSOLES = {
    "admin.html": (WEB / "admin.html").read_text(encoding="utf-8"),
    "jobs.html":  (WEB / "jobs.html").read_text(encoding="utf-8"),
}


def _fleet_entry(store_id: str) -> str:
    """One store's STORE_FLEETS entry, which may span several lines."""
    block = re.search(r"const STORE_FLEETS = \{(.+?)\n\};", SHARED, re.S)
    assert block, "STORE_FLEETS missing from admin-shared.js"
    m = re.search(rf"\n\s*{store_id}:\s*(.+?)(?=\n\s*[A-Z]+:|\Z)",
                  block.group(1), re.S)
    assert m, f"{store_id} missing from STORE_FLEETS in admin-shared.js"
    return m.group(1)


def _strip_comments(src: str) -> str:
    """Drop // and /* */ comments — a comment explaining the retired printer is
    documentation, not a hardcoded model."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


def test_osp_has_both_printers():
    entry = _fleet_entry("OSP")
    assert "konica: KONICA_PRO_1100" in entry
    assert "EPSON_EM_C8100" in entry      # spread, since it also carries `since`


@pytest.mark.parametrize("store_id", ["PRINTK", "PRIOFF"])
def test_no_konica_at_the_nattika_stores(store_id):
    entry = _fleet_entry(store_id)
    assert "konica: null" in entry, f"{store_id} must have no Konica"
    assert "epson: EPSON_EM_C8100" in entry


def test_the_epson_is_the_em_c8100():
    """The retired WF-C21000 is still named — old records have to be attributed
    to it — but no store may have it as its INSTALLED unit."""
    assert 'model: "EM-C8100"' in SHARED
    for store_id in ("OSP", "PRINTK", "PRIOFF"):
        entry = _fleet_entry(store_id)
        installed = entry.split("replaced:")[0]      # ignore the retired-unit link
        assert "WF_C21000" not in installed, f"{store_id} still runs the retired unit"
        assert "EPSON_EM_C8100" in installed


def test_queue_names_are_matched_by_model_family():
    """"EM-C8100 Series(Network)" carries no brand, so a bare "epson" test
    files every colour job under the Konica."""
    m = re.search(r"const EPSON_QUEUE_RE\s*=\s*(/.+?/i);", SHARED)
    assert m, "EPSON_QUEUE_RE missing from admin-shared.js"
    pattern = m.group(1)[1:-2]
    rx = re.compile(pattern, re.I)
    for queue in ("EM-C8100 Series(Network)", "EPSON EM-C8100 Series",
                  "WF-C21000 Series(Network)"):
        assert rx.search(queue), f"{queue!r} not recognised as the Epson"
    assert not rx.search("KONICA MINOLTA 1100 PS")


# ── Records are attributed to the unit that actually printed them ────────────

def test_osp_records_the_epson_swap_date():
    """Without a `since`, every one of OSP's 1,574 Epson job records — all from
    before 2026-06-29 — renders under the heading "Epson EM-C8100". That is what
    "OSP still shows 21000 details" was."""
    entry = _fleet_entry("OSP")
    assert 'since: "2026-06-29"' in entry
    assert "replaced: EPSON_WF_C21000" in entry


def test_the_retired_unit_is_named_so_old_rows_can_be_attributed():
    assert 'const EPSON_WF_C21000 = { key: "epson",  label: "Epson",  model: "WF-C21000" };' in SHARED


def test_a_store_with_no_swap_has_no_since():
    """Nattika has only ever had the one Epson, so nothing gets reattributed."""
    for store_id in ("PRINTK", "PRIOFF"):
        assert "since:" not in _fleet_entry(store_id)


def test_both_date_formats_are_handled():
    """job_date arrives as '2026-05-01 17:30' from delta rows and
    '2026.05.01 17:30' from the printer's own CSV — both must compare."""
    fn = SHARED[SHARED.index("function printerUnitAt"):]
    assert 'replace(/\./g, "-")' in fn, "dotted printer dates would sort wrong"
    assert ".slice(0, 10)" in fn


@pytest.mark.parametrize("page", sorted(CONSOLES))
def test_the_console_flags_a_table_that_is_all_old_unit(page):
    src = CONSOLES[page]
    assert "allRecordsPredateCurrentUnit(" in src
    assert "printerUnitAt(" in src, "each row must name the unit that printed it"


def test_the_admin_has_somewhere_to_put_the_warning():
    assert 'id="pjl-epson-note"' in CONSOLES["admin.html"]


def test_shared_helpers_are_defined_once():
    for fn in ("storeFleet", "printerViewStore", "storeHasKonica",
               "fleetPrinterKeys", "printerLabel", "printerShortLabel",
               "printerKeyFromName", "effectivePrinterKey",
               "printerUnitAt", "allRecordsPredateCurrentUnit"):
        assert SHARED.count(f"function {fn}(") == 1, f"{fn} should live only in admin-shared.js"


@pytest.mark.parametrize("page", sorted(CONSOLES))
def test_console_loads_the_shared_helpers(page):
    assert "admin-shared.js" in CONSOLES[page]


@pytest.mark.parametrize("page", sorted(CONSOLES))
def test_console_resolves_printers_through_the_fleet(page):
    src = CONSOLES[page]
    assert "printerViewStore(storeFilter)" in src, "printer views must follow the store"
    assert "effectivePrinterKey(" in src, "B&W items must resolve to the Epson where there is no Konica"
    # The substring test that mistook every EM-C8100 job for a Konica.
    assert 'includes("epson") ? "epson" : "konica"' not in src


@pytest.mark.parametrize("page", sorted(CONSOLES))
def test_console_does_not_hardcode_the_retired_printer(page):
    """Model names come from the fleet map, never from a literal in the page.
    Comments may explain the history."""
    src = _strip_comments(CONSOLES[page])
    assert "WF-C21000" not in src
    assert "192.168.55.202" not in src


@pytest.mark.parametrize("page", sorted(CONSOLES))
def test_console_hides_the_konica_section_when_there_is_none(page):
    src = CONSOLES[page]
    assert "fleetPrinterKeys(" in src
    # konica_jobs is not even requested at a store with no Konica.
    assert re.search(r'wantK \? sbFetch\("konica_jobs"', src)


@pytest.mark.parametrize("page", sorted(CONSOLES))
def test_console_reads_has_konica_from_the_store_pc(page):
    assert "data.has_konica" in CONSOLES[page]
