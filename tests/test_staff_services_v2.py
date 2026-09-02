"""
Services in order-v2 staff mode, booked through the Vercel API.

Owner decisions this file pins, both from 2026-09-02:

  * *"I absolutely hated the dark version… just add the missing features to
    v2."* — so lamination, binding, scanning and photocopying are booked from
    order-v2 staff mode, not from a console modal.
  * *"Use the vercel api so staff can work off-site."* — so they post to
    `/order/staff-service` and `/order/staff-photocopy`, never to the store
    PC's print_server on the LAN.

The pricing itself is not tested here — it is tested once, against rate_card,
in test_service_quote.py. What is tested here is that this page asks rather
than computing, and that the staff gate holds.
"""

import os
import re
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

import rate_card
import service_jobs


def order_v2():
    return open(os.path.join(ROOT, "website", "order-v2.html"), encoding="utf-8").read()


def order_ui():
    return open(os.path.join(ROOT, "website", "order", "order-ui.js"), encoding="utf-8").read()


def service_js():
    js = order_ui()
    return js[js.index("// ── Staff services"):js.index("function ensureStaffAuth()")]


# ── It goes to the cloud, not the counter ─────────────────────────────────────

def test_services_post_to_the_vercel_api():
    """The whole point of the owner's choice: a staff member at home, or at the
    other store, can still book a lamination."""
    js = service_js()
    assert "'/order/staff-service'" in js
    assert "'/order/staff-photocopy'" in js
    assert "API + url" in js


def test_the_page_never_calls_the_store_pc_directly():
    """print_server lives on the shop LAN on :3005. A call to it from here
    works at the counter and fails silently everywhere else."""
    js = order_ui()
    for lan in (":3005", "/new-service", "/new-photocopy"):
        assert lan not in js, f"order-ui.js reaches the store PC via {lan}"
    # print_server serves /service-quote; the cloud serves /order/service-quote.
    # Only the unprefixed form is the LAN one.
    assert not re.findall(r"(?<!/order)/service-quote", js)


def test_the_quote_comes_from_the_shared_endpoint():
    assert "/order/service-quote?" in service_js()


def test_the_page_does_not_price_anything_itself():
    """A second implementation of the rate card in JavaScript is how a shop
    starts quoting two different prices for the same lamination."""
    js = service_js()
    for token in ("ROLL_LAM_RATES", "BINDING_RATES", "SCANNING_TIERS",
                  "* rate", "rate *"):
        assert token not in js, f"the service panel computes a price ({token})"


def test_every_write_carries_the_staff_pin():
    js = service_js()
    body = re.search(r"async function submitService\(\) \{.*?\n\}", js, re.S).group(0)
    assert "'X-Staff-Pin': sessionStorage.getItem('staff_pin')" in body


def test_a_rejected_pin_says_so_rather_than_failing_vaguely():
    assert "if (r.status === 403)" in service_js()


# ── The kinds match the rate card ─────────────────────────────────────────────

def test_the_panel_offers_exactly_the_rate_cards_kinds():
    """A kind offered here that rate_card does not know is a 400 at the counter;
    one it knows that is missing here is revenue nobody can book."""
    ids = re.findall(r"\{ id: '(\w+)'", service_js())
    assert sorted(ids) == sorted(rate_card.SERVICE_KINDS)


def test_every_kind_has_a_quantity_label():
    js = service_js()
    labels = re.search(r"const SERVICE_QTY_LABEL = \{(.*?)\};", js, re.S).group(1)
    for kind in rate_card.SERVICE_KINDS:
        assert f"{kind}:" in labels, kind


def test_every_kind_declares_its_extra_fields():
    js = service_js()
    block = re.search(r"const SERVICE_FIELDS = \{(.*?)\n\};", js, re.S).group(1)
    for kind in rate_card.SERVICE_KINDS:
        assert re.search(rf"\b{kind}:\s*\[", block), kind


def test_a_field_that_means_nothing_for_a_kind_is_hidden_not_disabled():
    js = service_js()
    fn = re.search(r"function setServiceKind\(kind\) \{(.*?)\n\}", js, re.S).group(1)
    assert "style.display = on ? '' : 'none'" in fn


# ── A photocopy stays a photocopy ─────────────────────────────────────────────

def test_a_photocopy_goes_to_its_own_endpoint():
    """It is work the Konica actually did, so it is filed as a completed
    photocopy rather than a service job — which is what keeps it inside the
    printer counts that the B-10 reconciliation compares against."""
    js = service_js()
    assert "const isCopy = svc.kind === 'copy';" in js
    assert "isCopy ? '/order/staff-photocopy' : '/order/staff-service'" in js


def test_a_photocopy_sends_the_flat_body_that_endpoint_expects():
    js = service_js()
    body = re.search(r"if \(isCopy\) \{(.*?)\n  \}", js, re.S).group(1)
    for field in ("pages:", "copies:", "colour:", "sides:", "paper_size:", "is_student:"):
        assert field in body, field


# ── Refusing beats inventing ──────────────────────────────────────────────────

def test_an_unpriceable_service_asks_for_an_amount_rather_than_filing_zero():
    js = service_js()
    assert "This one has no rate — enter the amount taken." in js
    assert "if (svc.manual && !typed)" in js


def test_a_failed_quote_never_shows_an_invented_price():
    js = service_js()
    catch = re.search(r"\} catch \(e\) \{(.*?)\n  \}\n  syncServiceOverride", js, re.S).group(1)
    assert "Could not reach the rate card" in catch
    assert "svc.quote = null" in catch


def test_a_manual_price_is_quoted_at_what_was_taken():
    """Sending amount_quoted = 0 would read as a free job rather than an
    unpriced one, and the zero-priced-service alert would fire on every one."""
    assert "if (svc.manual && typed) body.amount_quoted = typed;" in service_js()


def test_the_quote_is_debounced_because_it_runs_while_someone_types():
    js = service_js()
    assert "function quoteServiceSoon()" in js
    assert "clearTimeout(svc.timer)" in js


def test_a_double_click_cannot_book_the_job_twice():
    js = service_js()
    assert "if (svc.busy) return;" in js
    assert "svc.busy = true; btn.disabled = true;" in js


# ── The deposit rule reaches the counter ──────────────────────────────────────

def test_the_waiver_box_appears_only_when_there_is_a_deposit_to_waive():
    js = service_js()
    fn = re.search(r"function syncServiceOverride\(\) \{(.*?)\n\}", js, re.S).group(1)
    assert "due > 0 && paid < due" in fn


def test_the_waiver_asks_for_a_reason_not_a_checkbox():
    """service_jobs.service_status only accepts a non-blank reason, and the
    placeholder says why."""
    page = order_v2()
    box = re.search(r'id="ov2-svc-override"[^>]*', page).group(0)
    assert "placeholder=" in box
    assert service_jobs.service_status(1000, 0, "") == service_jobs.STATUS_DRAFT
    assert service_jobs.service_status(1000, 0, "x") == service_jobs.STATUS_QUEUED


def test_the_deposit_is_shown_when_one_is_due():
    assert "deposit</b> before the work starts" in service_js()


# ── Staff only ────────────────────────────────────────────────────────────────

def test_the_whole_panel_starts_hidden():
    page = order_v2()
    for el_id in ("ov2-svc-switch", "ov2-svc-panel"):
        tag = re.search(rf'id="{el_id}"[^>]*', page).group(0)
        assert 'style="display:none"' in tag, el_id


def test_only_staff_mode_reveals_it():
    js = service_js()
    fn = re.search(r"function syncStaffServices\(\) \{(.*?)\n\}", js, re.S).group(1)
    assert "if (!STAFF) return;" in fn


def test_it_is_wired_up_from_the_staff_init():
    js = order_ui()
    init = js[js.index("  if (STAFF) {"):]
    assert "syncStaffServices();" in init
    assert "syncStaffScale();" in init


def test_switching_to_services_hides_the_print_flow():
    """A service has no file. Leaving the uploader on screen invites staff to
    attach one to a lamination."""
    js = service_js()
    fn = re.search(r"function setServiceMode\(on\) \{(.*?)\n\}", js, re.S).group(1)
    assert "panel.style.display = on ? '' : 'none'" in fn
    assert "print.style.display = on ? 'none' : ''" in fn
    assert 'id="ov2-print-panel"' in order_v2()


# ── The wiring actually resolves ──────────────────────────────────────────────

def test_every_element_the_panel_touches_exists():
    """A typo'd id is a silent no-op — the control simply does nothing, with no
    error anywhere. Cheaper to catch here than at the counter."""
    page = order_v2()
    ids = sorted(set(re.findall(r"\$\('([^']+)'\)", service_js())))
    assert ids, "the extraction is wrong, not the code"
    for el_id in ids:
        assert f'id="{el_id}"' in page, f"$('{el_id}') has no element"


def test_no_inline_handlers_because_this_is_a_module():
    """order-ui.js is an ES module, so its functions are not global — an inline
    onclick="" would silently never fire."""
    page = order_v2()
    panel = page[page.index('id="ov2-svc-switch"'):page.index('id="ov2-print-panel"')]
    assert not re.findall(r'\son\w+="', panel)


def test_the_output_is_escaped():
    """Breakdown lines and job ids are rendered with innerHTML."""
    js = service_js()
    assert "function escapeHtml(" in js
    assert "escapeHtml(b)" in js and "escapeHtml(d.job_id)" in js
