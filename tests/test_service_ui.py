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


def html(name="jobs.html"):
    return open(os.path.join(ROOT, "website", name), encoding="utf-8").read()


# ── The modal ─────────────────────────────────────────────────────────────────

def test_there_is_a_service_button_next_to_photocopy():
    src = html()
    assert 'onclick="openServiceModal()"' in src
    assert "+ Service" in src


def test_every_pill_is_a_kind_the_rate_card_can_price():
    block = re.search(r'id="svc-pills".*?</div>', html(), re.S).group(0)
    kinds = re.findall(r'data-kind="([a-z]+)"', block)
    assert kinds == list(SERVICE_KINDS), "modal pills drifted from rate_card.SERVICE_KINDS"


def test_every_pill_is_labelled_as_the_rate_card_labels_it():
    block = re.search(r'id="svc-pills".*?</div>', html(), re.S).group(0)
    pairs = re.findall(r'data-kind="([a-z]+)"[^>]*>([^<]+)<', block)
    for kind, label in pairs:
        assert label.strip() == SERVICE_KINDS[kind][0]


def test_the_field_map_covers_exactly_the_kinds():
    block = re.search(r"const SERVICE_FIELDS = \{.*?\n\};", html(), re.S).group(0)
    kinds = re.findall(r"^  ([a-z]+):", block, re.M)
    assert sorted(kinds) == sorted(SERVICE_KINDS)


def test_every_field_named_by_a_kind_exists_in_the_markup():
    src = html()
    block = re.search(r"const SERVICE_FIELDS = \{.*?\n\};", src, re.S).group(0)
    named = set(re.findall(r'"([a-z-]+)"', block))
    for field in named:
        assert f'id="svc-f-{field}"' in src, f"kind asks for {field}, no such field"


def test_the_toggle_list_covers_every_field_a_kind_can_ask_for():
    """A field left out of SERVICE_ALL_FIELDS would never be hidden again."""
    src = html()
    used = set(re.findall(r'"([a-z-]+)"',
                          re.search(r"const SERVICE_FIELDS = \{.*?\n\};", src, re.S).group(0)))
    toggled = set(re.findall(r'"([a-z-]+)"',
                             re.search(r"const SERVICE_ALL_FIELDS = \[.*?\n\];", src, re.S).group(0)))
    assert used <= toggled


def test_the_modal_never_prices_anything_itself():
    """One rate card, one answer — the console asks the store PC."""
    src = html()
    block = src[src.index("function serviceMeta()"):src.index("async function confirmService()")]
    assert "/service-quote" in block
    for giveaway in ("Rs.30", "* 70", "* 15", "PRICE", "RATE"):
        assert giveaway not in block, f"a rate leaked into the console: {giveaway}"


def test_an_unpriceable_service_asks_for_a_typed_price():
    src = html()
    assert 'id="svc-f-manual"' in src
    block = src[src.index("async function refreshServiceQuote()"):src.index("async function confirmService()")]
    assert "needs_manual_price" in block
    assert 'document.getElementById("svc-f-manual").style.display = needsPrice ? "block" : "none"' in block


def test_an_unreachable_store_pc_says_so_rather_than_showing_nothing():
    src = html()
    block = src[src.index("async function refreshServiceQuote()"):src.index("async function confirmService()")]
    assert "Print server not configured" in block
    assert "Cannot reach print server" in block
    # ...and still lets the operator type a price so the counter is not stuck.
    assert block.count('document.getElementById("svc-f-manual").style.display = "block"') == 2


def test_the_deposit_is_shown_before_the_job_is_booked():
    src = html()
    assert 'id="svc-deposit"' in src
    assert "Deposit before work starts" in src
    assert "deposit is due before the work starts" in src   # and again on Draft


def test_booking_posts_to_new_service_not_create_job():
    src = html()
    block = src[src.index("async function confirmService()"):src.index("// ── Service Panel")]
    assert "/new-service" in block
    assert "/create-job" not in block
    assert "/print" not in block


# ── The panel ─────────────────────────────────────────────────────────────────

def test_select_job_branches_to_the_service_panel_before_anything_print():
    src = html()
    body = src[src.index("function selectJob(jobId)"):src.index("// ── Job Timeline")]
    branch = body.index("if (isServiceJob(job))")
    for print_thing in ("editItems = [{", "/job-items", "renderJobPanel()", "panelHTML()"):
        assert body.index(print_thing) > branch, \
            f"{print_thing} runs before the service branch"
    assert "return;" in body[branch:branch + 900]


def test_the_service_panel_offers_no_way_to_print():
    src = html()
    panel = src[src.index("function servicePanelHTML(j)"):src.index("const SERVICE_LABELS")]
    for print_thing in ("printItem(", "jp-btn-save", "jp-scale", "detect-colour",
                        "jp-items", "addItem("):
        assert print_thing not in panel, f"the service panel exposes {print_thing}"


def test_the_service_panel_keeps_the_ready_and_collect_ids():
    """markReady() and openPaymentModal() are reused unchanged, so the ids they
    reach for have to be the ones they already reach for."""
    src = html()
    panel = src[src.index("function servicePanelHTML(j)"):src.index("const SERVICE_LABELS")]
    for shared_id in ('id="jp-btn-notify"', 'id="jp-status"', 'id="jp-id"'):
        assert shared_id in panel
    assert "markReady()" in panel and "openPaymentModal()" in panel


def test_the_panel_does_not_offer_the_pre_print_paid_buttons():
    """markPaid() posts to the cloud and says 'this sends it to the printer' —
    wrong on both counts for a service. Payment is on collection."""
    src = html()
    panel = src[src.index("function servicePanelHTML(j)"):src.index("const SERVICE_LABELS")]
    assert "markPaid(" not in panel


def test_panel_labels_match_the_rate_card():
    block = re.search(r"const SERVICE_LABELS = \{.*?\n\};", html(), re.S).group(0)
    pairs = dict(re.findall(r'([a-z]+): "([^"]+)"', block))
    assert pairs == {k: v[0] for k, v in SERVICE_KINDS.items()}


def test_service_meta_is_read_whether_it_arrives_as_json_or_text():
    """Supabase returns jsonb as an object; a local row holds a string."""
    src = html()
    fn = src[src.index("function _serviceMetaOf(j)"):src.index("function _svcPretty")]
    assert 'typeof raw === "object"' in fn
    assert "JSON.parse" in fn


def test_a_print_job_is_untouched_by_any_of_this():
    """isServiceJob is the only gate, and it is false for every existing job."""
    src = html()
    fn = src[src.index("function isServiceJob(job)"):src.index("function servicePanelHTML")]
    assert "return !!(job && job.service_kind);" in fn
