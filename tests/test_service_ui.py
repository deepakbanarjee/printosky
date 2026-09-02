"""
B-4 — the service console in jobs.html.

Two things are worth pinning about a console built out of HTML strings:

  1. **The kinds cannot drift from the rate card.** Every pill in the modal must
     be a kind `calculate_service_quote` knows how to price. A kind that is
     orderable but unpriceable is the exact shape of the bug that made five
     finishings bill Rs.0.
  2. **The service panel is a separate panel, not the print panel with parts
     hidden.** `selectJob` returns before it touches print items, scaling or
     colour detection — a print panel with the printing hidden is a print panel
     someone eventually prints from.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from rate_card import SERVICE_KINDS

ROOT = os.path.join(os.path.dirname(__file__), "..")


CONSOLES = ("jobs.html", "admin.html")


def html(name="jobs.html"):
    return open(os.path.join(ROOT, "website", name), encoding="utf-8").read()


# ── The modal ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", CONSOLES)
def test_there_is_a_service_button_next_to_photocopy(name):
    src = html(name)
    assert 'onclick="openServiceModal()"' in src
    assert "+ Service" in src


@pytest.mark.parametrize("name", CONSOLES)
def test_every_pill_is_a_kind_the_rate_card_can_price(name):
    block = re.search(r'id="svc-pills".*?</div>', html(name), re.S).group(0)
    kinds = re.findall(r'data-kind="([a-z]+)"', block)
    assert kinds == list(SERVICE_KINDS), "modal pills drifted from rate_card.SERVICE_KINDS"


@pytest.mark.parametrize("name", CONSOLES)
def test_every_pill_is_labelled_as_the_rate_card_labels_it(name):
    block = re.search(r'id="svc-pills".*?</div>', html(name), re.S).group(0)
    pairs = re.findall(r'data-kind="([a-z]+)"[^>]*>([^<]+)<', block)
    for kind, label in pairs:
        assert label.strip() == SERVICE_KINDS[kind][0]


@pytest.mark.parametrize("name", CONSOLES)
def test_the_field_map_covers_exactly_the_kinds(name):
    block = re.search(r"const SERVICE_FIELDS = \{.*?\n\};", html(name), re.S).group(0)
    kinds = re.findall(r"^  ([a-z]+):", block, re.M)
    assert sorted(kinds) == sorted(SERVICE_KINDS)


@pytest.mark.parametrize("name", CONSOLES)
def test_every_field_named_by_a_kind_exists_in_the_markup(name):
    src = html(name)
    block = re.search(r"const SERVICE_FIELDS = \{.*?\n\};", src, re.S).group(0)
    named = set(re.findall(r'"([a-z-]+)"', block))
    for field in named:
        assert f'id="svc-f-{field}"' in src, f"kind asks for {field}, no such field"


@pytest.mark.parametrize("name", CONSOLES)
def test_the_toggle_list_covers_every_field_a_kind_can_ask_for(name):
    """A field left out of SERVICE_ALL_FIELDS would never be hidden again."""
    src = html(name)
    used = set(re.findall(r'"([a-z-]+)"',
                          re.search(r"const SERVICE_FIELDS = \{.*?\n\};", src, re.S).group(0)))
    toggled = set(re.findall(r'"([a-z-]+)"',
                             re.search(r"const SERVICE_ALL_FIELDS = \[.*?\n\];", src, re.S).group(0)))
    assert used <= toggled


@pytest.mark.parametrize("name", CONSOLES)
def test_the_modal_never_prices_anything_itself(name):
    """One rate card, one answer — the console asks the store PC."""
    src = html(name)
    block = src[src.index("function serviceMeta()"):src.index("async function confirmService()")]
    assert "/service-quote" in block
    for giveaway in ("Rs.30", "* 70", "* 15", "PRICE", "RATE"):
        assert giveaway not in block, f"a rate leaked into the console: {giveaway}"


@pytest.mark.parametrize("name", CONSOLES)
def test_an_unpriceable_service_asks_for_a_typed_price(name):
    src = html(name)
    assert 'id="svc-f-manual"' in src
    block = src[src.index("async function refreshServiceQuote()"):src.index("async function confirmService()")]
    assert "needs_manual_price" in block
    assert 'document.getElementById("svc-f-manual").style.display = needsPrice ? "block" : "none"' in block


@pytest.mark.parametrize("name", CONSOLES)
def test_an_unreachable_store_pc_says_so_rather_than_showing_nothing(name):
    src = html(name)
    block = src[src.index("async function refreshServiceQuote()"):src.index("async function confirmService()")]
    assert "Print server not configured" in block
    assert "Cannot reach print server" in block
    # ...and still lets the operator type a price so the counter is not stuck.
    assert block.count('document.getElementById("svc-f-manual").style.display = "block"') == 2


@pytest.mark.parametrize("name", CONSOLES)
def test_the_deposit_is_shown_before_the_job_is_booked(name):
    src = html(name)
    assert 'id="svc-deposit"' in src
    assert "Deposit before work starts" in src
    assert "deposit is due before the work starts" in src   # and again on Draft


@pytest.mark.parametrize("name", CONSOLES)
def test_booking_posts_to_new_service_not_create_job(name):
    src = html(name)
    block = src[src.index("async function confirmService()"):src.index("// ── Service Panel")]
    assert "/new-service" in block
    assert "/create-job" not in block
    assert "/print" not in block


# ── The panel ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", CONSOLES)
def test_select_job_branches_to_the_service_panel_before_anything_print(name):
    src = html(name)
    body = src[src.index("function selectJob(jobId)"):src.index("// ── Job Timeline")]
    branch = body.index("if (isServiceJob(job))")
    for print_thing in ("editItems = [{", "/job-items", "renderJobPanel()", "panelHTML()"):
        assert body.index(print_thing) > branch, \
            f"{print_thing} runs before the service branch"
    assert "return;" in body[branch:branch + 900]


@pytest.mark.parametrize("name", CONSOLES)
def test_the_service_panel_offers_no_way_to_print(name):
    src = html(name)
    panel = src[src.index("function servicePanelHTML(j)"):src.index("const SERVICE_LABELS")]
    for print_thing in ("printItem(", "jp-btn-save", "jp-scale", "detect-colour",
                        "jp-items", "addItem("):
        assert print_thing not in panel, f"the service panel exposes {print_thing}"


@pytest.mark.parametrize("name", CONSOLES)
def test_the_service_panel_keeps_the_ready_and_collect_ids(name):
    """markReady() and openPaymentModal() are reused unchanged, so the ids they
    reach for have to be the ones they already reach for."""
    src = html(name)
    panel = src[src.index("function servicePanelHTML(j)"):src.index("const SERVICE_LABELS")]
    for shared_id in ('id="jp-btn-notify"', 'id="jp-status"', 'id="jp-id"'):
        assert shared_id in panel
    assert "markReady()" in panel and "openPaymentModal()" in panel


@pytest.mark.parametrize("name", CONSOLES)
def test_the_panel_does_not_offer_the_pre_print_paid_buttons(name):
    """markPaid() posts to the cloud and says 'this sends it to the printer' —
    wrong on both counts for a service. Payment is on collection."""
    src = html(name)
    panel = src[src.index("function servicePanelHTML(j)"):src.index("const SERVICE_LABELS")]
    assert "markPaid(" not in panel


@pytest.mark.parametrize("name", CONSOLES)
def test_panel_labels_match_the_rate_card(name):
    block = re.search(r"const SERVICE_LABELS = \{.*?\n\};", html(name), re.S).group(0)
    pairs = dict(re.findall(r'([a-z]+): "([^"]+)"', block))
    assert pairs == {k: v[0] for k, v in SERVICE_KINDS.items()}


@pytest.mark.parametrize("name", CONSOLES)
def test_service_meta_is_read_whether_it_arrives_as_json_or_text(name):
    """Supabase returns jsonb as an object; a local row holds a string."""
    src = html(name)
    fn = src[src.index("function _serviceMetaOf(j)"):src.index("function _svcPretty")]
    assert 'typeof raw === "object"' in fn
    assert "JSON.parse" in fn


@pytest.mark.parametrize("name", CONSOLES)
def test_a_print_job_is_untouched_by_any_of_this(name):
    """isServiceJob is the only gate, and it is false for every existing job."""
    src = html(name)
    fn = src[src.index("function isServiceJob(job)"):src.index("function servicePanelHTML")]
    assert "return !!(job && job.service_kind);" in fn


# ── The two consoles are mirrors ──────────────────────────────────────────────
#
# Drift between jobs.html and admin.html is a known problem in this repo, so the
# service blocks are compared against each other rather than each being checked
# in isolation.

def _block(src, start, end):
    i = src.index(start)
    return src[i:src.index(end, i)]


def test_the_service_javascript_is_identical_in_both_consoles():
    j = _block(html("jobs.html"),
               "// ── Service Modal — post-press work with no printing (B-4)",
               "// ── Photocopy Modal ─")
    a = _block(html("admin.html"),
               "// ── Service Modal — post-press work with no printing (B-4)",
               "// ── Photocopy Modal ─")
    assert j == a, "the service JavaScript has drifted between the consoles"


def test_the_service_modal_markup_is_identical_in_both_consoles():
    j = _block(html("jobs.html"), "<!-- ── Service Modal", "<!-- ── Photocopy Modal ── -->")
    a = _block(html("admin.html"), "<!-- ── Service Modal", "<!-- ── Photocopy Modal ── -->")
    assert j == a, "the service modal markup has drifted between the consoles"


def test_the_service_styles_are_identical_in_both_consoles():
    j = _block(html("jobs.html"), "  /* ── Service Modal + Service Panel",
               "  /* ── New Job Modal (wizard)")
    a = _block(html("admin.html"), "  /* ── Service Modal + Service Panel",
               "  /* ── New Job Modal (wizard)")
    assert j == a, "the service CSS has drifted between the consoles"


# ── Service jobs are not print jobs, and no panel may count them as one ───────

@pytest.mark.parametrize("name", CONSOLES)
def test_the_printer_breakdown_excludes_service_jobs(name):
    """A lamination booked at the counter is not a Konica job.

    renderPrinterBreakdown() buckets every one of today's jobs into a printer
    panel via guessprinter(), which has no idea what a service job is — so the
    filter has to happen before it.
    """
    fn = _block(html(name), "function renderPrinterBreakdown()", "\n  [\"konica\", \"epson\"]")
    assert "if (j.service_kind) return false;" in fn
    # ...and before anything decides which printer the job belongs to.
    assert fn.index("if (j.service_kind) return false;") < fn.index("guessprinter") \
        if "guessprinter" in fn else True


@pytest.mark.parametrize("name", CONSOLES)
def test_the_stat_cards_still_count_service_jobs(name):
    """Revenue and job counts are not print counts — a service job is a job.

    This is the deliberate other half of the filter above: money taken for
    lamination is money taken, and pending work is pending work.
    """
    src = html(name)
    fn = _block(src, "function renderStats(", "\n}")
    assert "service_kind" not in fn
    # ...and the shared summariser that feeds it does not filter them out either.
    shared = open(os.path.join(ROOT, "website", "admin-shared.js"), encoding="utf-8").read()
    summarise = _block(shared, "function summarizeJobs(", "\n}")
    assert "service_kind" not in summarise


def test_mis_staff_panels_cannot_see_a_service_job():
    """MIS's staff panels read jobs only where printed_by is set, and nothing
    sets it on a service job — so the exclusion is structural, not a filter
    someone can drop.

    Updated for B-10 (2026-09-02). This asserted the gate on *every* MIS jobs
    query, using "every query" as a proxy for "every staff query". B-10's
    reconciliation panel is the case that separates them: it exists to count
    copy/scan **service** jobs, so gating it on printed_by would hide exactly
    the rows it is there to find. The property being pinned is unchanged — the
    staff panels cannot see a service job — but it is now stated about the
    staff queries rather than about all of them, and the reconciliation queries
    are pinned from the other side: they must never carry the gate.
    """
    mis = html("mis.html")
    job_queries = re.findall(r'sbFetch\("jobs",\s*`([^`]+)`', mis)
    assert job_queries, "expected MIS to read the jobs table"

    staff = [q for q in job_queries if "printed_by" in q]
    recon = [q for q in job_queries if "${RC_SELECT}" in q]
    assert staff, "expected MIS to read jobs for the staff panels"
    assert recon, "expected MIS to read jobs for the reconciliation panel"
    assert len(staff) + len(recon) == len(job_queries), (
        f"an MIS jobs query is neither a staff nor a reconciliation read: "
        f"{set(job_queries) - set(staff) - set(recon)}")

    for q in staff:
        assert "printed_by=not.is.null" in q, f"staff query without the gate: {q}"
    for q in recon:
        assert "printed_by" not in q, (
            f"the reconciliation counts service jobs; the printed_by gate would "
            f"hide every one of them: {q}")

    # The claim above only holds while nothing writes printed_by on a service job.
    server = open(os.path.join(ROOT, "print_server.py"), encoding="utf-8").read()
    new_service = _block(server, "def handle_new_service(", "def _alert_zero_priced_service(")
    assert "printed_by" not in new_service


def test_mis_printer_counts_come_from_the_machines_not_from_jobs():
    """The page-count breakdown reads printer_counters and konica_jobs — machine
    data a service job cannot appear in. Worth pinning: the tempting "fix" is to
    recount those from the jobs table, which would quietly include services.

    B-10 note: the reconciliation panel reads both sides on purpose, and that is
    the one place they are allowed to meet. It compares them; it never sums them
    into a printer count.
    """
    mis = html("mis.html")
    assert 'sbFetch("printer_counters"' in mis
    assert 'sbFetch("konica_jobs"' in mis

    # Machine page counts are never derived from the jobs table.
    for fn in ("renderCounterStats", "renderBreakdown", "renderKJPeriod"):
        body = _block(mis, f"function {fn}(", "\n}")
        assert "sbFetch" not in body, f"{fn} must be handed machine rows, not fetch jobs"

    # Every jobs read is either a staff-performance one (gated on printed_by) or
    # a reconciliation one (deliberately not) — no third kind has crept in.
    assert mis.count('sbFetch("jobs"') == len(
        re.findall(r'sbFetch\("jobs",\s*`[^`]*printed_by=not\.is\.null[^`]*`', mis)
    ) + len(
        re.findall(r'sbFetch\("jobs",\s*`[^`]*\$\{RC_SELECT\}[^`]*`', mis)
    )
