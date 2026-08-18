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
    m = re.search(rf"^\s*{store_id}:\s*\{{(.+?)\}},\s*$", SHARED, re.M)
    assert m, f"{store_id} missing from STORE_FLEETS in admin-shared.js"
    return m.group(1)


def test_osp_has_both_printers():
    entry = _fleet_entry("OSP")
    assert "konica: KONICA_PRO_1100" in entry
    assert "epson: EPSON_EM_C8100" in entry


@pytest.mark.parametrize("store_id", ["PRINTK", "PRIOFF"])
def test_no_konica_at_the_nattika_stores(store_id):
    entry = _fleet_entry(store_id)
    assert "konica: null" in entry, f"{store_id} must have no Konica"
    assert "epson: EPSON_EM_C8100" in entry


def test_the_epson_is_the_em_c8100():
    """The retired WF-C21000 may be named in comments and in the queue-name
    regex (old rows still match), but never as an installed model."""
    assert 'model: "EM-C8100"' in SHARED
    assert 'model: "WF-C21000"' not in SHARED
    code = "\n".join(l for l in SHARED.splitlines() if not l.lstrip().startswith(("//", "*", "/*")))
    assert "WF-C21000" not in code


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


def test_shared_helpers_are_defined_once():
    for fn in ("storeFleet", "printerViewStore", "storeHasKonica",
               "fleetPrinterKeys", "printerLabel", "printerShortLabel",
               "printerKeyFromName", "effectivePrinterKey"):
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
    src = CONSOLES[page]
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
