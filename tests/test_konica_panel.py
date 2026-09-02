"""
The MIS Konica panel and its reconciliation — structure, and the three bugs.

For five months this panel rendered plausible numbers that were five months
stale, because three field-shape divergences each looked harmless on its own.
These tests pin the fixes to the file, so the next person to touch the queries
finds out here rather than in a quarter's revenue.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MIS = ROOT / "website" / "mis.html"


@pytest.fixture(scope="module")
def mis() -> str:
    return MIS.read_text(encoding="utf-8")


def _script(mis: str) -> str:
    m = re.search(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", mis, re.S)
    assert m, "mis.html has no inline script"
    return m.group(1)


def _code(mis: str) -> str:
    """The script with `//` comments stripped.

    The comments explaining these fixes quote the code they replaced, so an
    assertion about what the file no longer *does* has to read the code and not
    the prose about it.
    """
    return "\n".join(re.sub(r"//.*$", "", line)
                     for line in _script(mis).splitlines())


# ── The panel exists and is wired ─────────────────────────────────────────────

@pytest.mark.parametrize("period", ["today", "week", "month", "year"])
def test_the_panel_has_a_pane_per_window(mis, period):
    assert f'id="rc-{period}"' in mis


def test_the_panel_has_a_tab_per_window(mis):
    tabs = re.search(r'id="rc-tabs">(.*?)</div>', mis, re.S)
    assert tabs
    for period in ("today", "week", "month", "year"):
        assert f"setRCTab('{period}'" in tabs.group(1)


def test_every_pane_is_rendered_on_load(mis):
    assert '["today","week","month","year"].forEach(renderReconPeriod);' in mis


def test_the_renderer_and_its_tab_switch_are_defined(mis):
    body = _script(mis)
    for fn in ("function renderReconPeriod(", "function setRCTab(",
               "function reconcile(", "function kjFreshness("):
        assert fn in body, fn


def test_only_one_pane_starts_active(mis):
    panes = re.findall(r'id="rc-(\w+)" class="jobs-period-content( active)?"', mis)
    assert [p for p, active in panes if active] == ["today"]


# ── Bug 1: the result filter that matched only the retired writer ─────────────

def test_no_konica_query_filters_on_a_single_writers_result_vocabulary(mis):
    """`result=eq.No Error` matched `No Error` only. The live fetcher writes
    `OK`, so from 2026-04-13 the panel saw only the CSV importer's rows."""
    for q in re.findall(r'sbFetch\("konica_jobs", `([^`]*)`', mis):
        assert "result=" not in q, q


def test_completion_is_decided_by_a_function_that_knows_both_vocabularies(mis):
    body = _script(mis)
    ok = re.search(r"const KJ_RESULT_OK = new Set\(\[(.*?)\]\)", body).group(1)
    assert '"OK"' in ok and '"NOERROR"' in ok


def test_cancelled_work_is_still_excluded(mis):
    """Dropping the server-side filter must not mean counting cancelled jobs."""
    body = _script(mis)
    assert "if (!kjOk(r)) return false;" in body


# ── Bug 2: slash dates passing every window filter ────────────────────────────

def test_the_window_is_decided_in_the_browser_not_by_string_comparison(mis):
    body = _script(mis)
    assert "function kjDay(" in body
    assert "function kjUsable(" in body
    for period in ("today", "week", "month", "year"):
        assert re.search(rf"{period}:\s*kjUsable\(", body), period


def test_kjday_understands_both_separators(mis):
    body = _script(mis)
    pattern = re.search(r"const m = raw\.match\((/.*?/)\);", body).group(1)
    assert "[/-]" in pattern


def test_a_row_with_no_readable_date_is_skipped_not_placed(mis):
    body = _script(mis)
    assert "return day !== null && day >= startDay;" in body


# ── Bug 3: the exact-case job_type buckets ────────────────────────────────────

def test_the_breakdown_no_longer_compares_job_type_exactly(mis):
    body = _code(mis)
    assert 'r.job_type === "Print"' not in body
    assert 'r.job_type === "Copy"' not in body
    assert 'const t = kjType(r);' in body


def test_kjtype_accepts_either_casing(mis):
    body = _script(mis)
    fn = re.search(r"function kjType\(row\) \{(.*?)\n\}", body, re.S).group(1)
    assert ".toUpperCase()" in fn
    assert "KJ_TYPES.find" in fn


def test_scan_jobs_are_measured_by_num_pages(mis):
    body = _script(mis)
    assert 'if (t === "Scan")  { scanPages  += (r.num_pages || 0); scanJobs++; }' in body


def test_the_panel_shows_scans_at_all(mis):
    """811 scan jobs existed and the panel had no card for them."""
    assert "Scan Jobs" in mis


# ── The reconciliation's honesty rules ────────────────────────────────────────

def test_no_machine_data_says_so_rather_than_reporting_nothing_unbilled(mis):
    body = _script(mis)
    blind = re.search(r'if \(r\.status === "blind"\) \{(.*?)\} else if', body, re.S).group(1)
    assert "nothing to reconcile" in blind
    assert "not the same as" in blind


def test_a_stale_machine_log_is_announced_over_any_verdict(mis):
    """A reconciliation running on a dead feed reports "nothing unbilled",
    which is precisely the failure the panel exists to catch."""
    body = _script(mis)
    assert "RC_STALE_HOURS" in body
    stale = re.search(r"if \(stale\) \{(.*?)\n  \}", body, re.S).group(1)
    assert 'cls = "blind";' in stale
    assert "old" in stale


def test_the_counter_fetch_covers_every_window(mis):
    for window in ("dToday", "dWeek", "dMonth", "dYear"):
        assert re.search(rf'sbFetch\("jobs", `received_at=gte\.\$\{{{window}\}}'
                         rf'&select=\$\{{RC_SELECT\}}', mis), window


def test_the_counter_select_carries_both_paths_fields(mis):
    fields = re.search(r'const RC_SELECT = "(.*?)"', mis).group(1).split(",")
    for f in ("service_kind", "service_meta", "source", "service_type",
              "page_count", "copies"):
        assert f in fields, f


def test_a_failed_counter_fetch_degrades_rather_than_blanking_the_page(mis):
    for q in re.findall(r'sbFetch\("jobs", `received_at[^`]*`\)(\.catch\(\(\)=>\[\]\))?', mis):
        assert q, "a counter fetch without .catch() takes the whole page down"


# ── Tab bars are addressed by id, not by position ─────────────────────────────

def test_no_tab_handler_selects_its_bar_by_position(mis):
    """`querySelectorAll(".tab-bar")[2]` on a page with two bars was undefined,
    and the `&&` guard turned that into silence: staff tabs accumulated the
    active class instead of switching. Inserting a panel above would have fixed
    it by accident and broken it again on the next insertion."""
    body = _code(mis)
    assert not re.search(r'querySelectorAll\("\.tab-bar"\)\[\d+\]', body)


@pytest.mark.parametrize("bar", ["kj-tabs", "rc-tabs", "sp-tabs"])
def test_every_tab_bar_has_an_id(mis, bar):
    assert f'id="{bar}"' in mis


def test_every_tab_switch_clears_the_other_buttons(mis):
    body = _code(mis)
    for fn, bar in (("setRCTab", "rc-tabs"), ("setSPTab", "sp-tabs")):
        block = re.search(rf"function {fn}\(.*?\n\}}", body, re.S).group(0)
        assert bar in block, f"{fn} does not address {bar}"
        assert 'classList.remove("active")' in block, fn
