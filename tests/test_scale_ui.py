"""
The staff scaling control in both consoles — A-5 of the scaling plan.

Two things worth pinning. First, the control must default to "no scaling", so
opening a job and saving it does not quietly start scaling something that was
printing fine. Second, jobs.html and admin.html are mirrors of one panel, and
drift between them is a known problem in this repo — so the blocks are compared
against each other rather than each being checked in isolation.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
CONSOLES = ("jobs.html", "admin.html")


def html(name):
    return open(os.path.join(ROOT, "website", name), encoding="utf-8").read()


@pytest.mark.parametrize("name", CONSOLES)
class TestTheControl:

    def test_the_select_exists_with_all_four_choices(self, name):
        block = re.search(r'id="jp-scale-mode".*?</select>', html(name), re.S)
        assert block, "no scale control in the print panel"
        values = re.findall(r'<option value="([a-z]*)"', block.group(0))
        assert values == ["", "fit", "actual", "custom"]

    def test_the_control_is_labelled_scale_option(self, name):
        """Owner's wording, 2026-09-01.

        It was "Page size on paper", which described what the control does but
        not what anyone at the counter calls it — the owner could not find
        Custom % because nothing on the page said "scale". A label nobody
        searches for is a feature nobody uses.
        """
        block = re.search(r'<label>([^<]+)</label>\s*<select class="pp-sel" id="jp-scale-mode"',
                          html(name))
        assert block, "no label immediately above the scale control"
        assert block.group(1).strip() == "Scale option"

    def test_the_default_is_no_scaling(self, name):
        """The first option is the empty one, so a panel opened and saved
        without touching this leaves the job exactly as it was."""
        block = re.search(r'id="jp-scale-mode".*?</select>', html(name), re.S).group(0)
        assert re.search(r'<option value=""[^>]*>Printer default', block)
        assert 'let editScaleMode = "";' in html(name)

    def test_custom_offers_the_owners_presets(self, name):
        block = re.search(r'id="jp-scale-custom".*?</div>', html(name), re.S).group(0)
        assert [int(p) for p in re.findall(r'setScalePercent\((\d+)\)', block)] == \
               [50, 75, 90, 125, 150, 200]

    def test_the_percent_box_is_bounded(self, name):
        box = re.search(r'id="jp-scale-percent"[^>]*', html(name)).group(0)
        assert 'min="25"' in box and 'max="400"' in box

    def test_the_price_note_is_shown(self, name):
        """Scaling does not change the bill, and the operator should not have
        to be asked twice."""
        assert "Price is per sheet — scaling does not change it." in html(name)

    def test_it_is_saved_with_the_specs(self, name):
        assert "scale_mode:    editScaleMode || null," in html(name)
        assert 'scale_percent: editScaleMode === "custom" ? editScalePercent : null,' in html(name)

    def test_it_is_restored_when_the_panel_opens(self, name):
        assert 'editScaleMode    = firstItem.scale_mode || "";' in html(name)
        assert "editScalePercent = firstItem.scale_percent || null;" in html(name)


@pytest.mark.parametrize("name", CONSOLES)
class TestThePreview:

    def test_it_fetches_the_baked_render(self, name):
        assert "/scale-preview?" in html(name)

    def test_it_reads_the_metadata_headers(self, name):
        page = html(name)
        for header in ("X-Total-Pages", "X-Page", "X-Cropped-Pages"):
            assert header in page

    def test_it_is_debounced_and_cancels_superseded_renders(self, name):
        page = html(name)
        assert "AbortController" in page
        assert "clearTimeout(scalePreviewT)" in page

    def test_a_failure_says_so_rather_than_showing_something(self, name):
        page = html(name)
        assert "Preview unavailable" in page
        assert "Store PC unreachable — preview unavailable" in page

    def test_an_unscalable_job_disables_the_control(self, name):
        """404/415 mean scaling cannot work here at all, so the control must
        not sit there looking usable while doing nothing."""
        assert "if (r.status === 404 || r.status === 415)" in html(name)

    def test_no_scaling_keeps_the_ordinary_file_view(self, name):
        assert "if (!editScaleMode) {" in html(name)

    def test_the_crop_warning_counts_pages(self, name):
        assert "pages will be cropped" in html(name)

    def test_object_urls_are_revoked(self, name):
        """A counter left on a job all day must not leak a blob per keystroke."""
        assert "URL.revokeObjectURL(old)" in html(name)


@pytest.mark.parametrize("name", CONSOLES)
class TestEveryIdItTouchesExists:
    """A typo'd getElementById is a silent no-op — the control would simply do
    nothing, with no error anywhere. Cheaper to catch here than at the counter."""

    SCALE_FUNCS = ("setScaleMode", "setScalePercent", "stepPreviewPage",
                   "scaleMsg", "refreshScalePreview")

    def _scale_js(self, name):
        page = html(name)
        start = page.index("// ── Page scaling (staff only)")
        return page[start:page.index("// ── Save specs", start)]

    def test_all_functions_are_defined(self, name):
        js = self._scale_js(name)
        for fn in self.SCALE_FUNCS:
            assert f"function {fn}(" in js, fn

    def test_every_element_id_referenced_exists_in_the_markup(self, name):
        page = html(name)
        js = self._scale_js(name)
        ids = set(re.findall(r'getElementById\("([^"]+)"\)', js))
        assert ids, "no ids found — the extraction is wrong, not the code"
        for el_id in sorted(ids):
            assert f'id="{el_id}"' in page, f"{name}: getElementById({el_id!r}) has no element"

    def test_every_handler_the_markup_calls_is_defined(self, name):
        page = html(name)
        js = self._scale_js(name)
        block = re.search(r'id="jp-scale-mode".*?jp-scale-note.*?</div>', page, re.S).group(0)
        for call in set(re.findall(r'on\w+="(\w+)\(', block)):
            assert f"function {call}(" in js or f"function {call}(" in page, call

    def test_the_querySelectorAll_target_exists(self, name):
        js = self._scale_js(name)
        for sel in re.findall(r'querySelectorAll\("([^"]+)"\)', js):
            root = sel.split()[0].lstrip("#").split(".")[0]
            assert f'id="{root}"' in html(name), sel


class TestTheTrialPageIsRetired:
    """order-v3.html was the scaling trial; order-v2.html is the page every
    link, WhatsApp message and search result points at. The trial has been
    folded into the live page (owner, 2026-08-31), so the trial file must be
    gone and its URL must land on the real page rather than 404.

    The inertness guards stay: order.html and any future page can still load
    order-ui.js without the scale markup, and the default must still send
    nothing, which is what keeps pre-scaling orders planning identically."""

    def test_the_trial_page_is_gone(self):
        assert not os.path.exists(os.path.join(ROOT, "website", "order-v3.html"))

    def test_the_trial_url_redirects_to_the_live_page(self):
        rules = open(os.path.join(ROOT, "website", "_redirects"), encoding="utf-8").read()
        for src in ("/order-v3", "/order-v3.html"):
            assert re.search(rf"^{re.escape(src)}\s+/order-v2\.html\s+301$", rules, re.M), src

    def test_the_shared_javascript_is_inert_without_the_markup(self):
        """Every entry point the scaling code has must survive a missing
        element, because a page can load the same module and have none of them."""
        js = open(os.path.join(ROOT, "website", "order", "order-ui.js"), encoding="utf-8").read()
        start = js.index("// \u2500\u2500 Page scaling (Fit / Actual size)")
        block = js[start:js.index("function setDirection", start)]
        assert "if (!sheet || !page || !cap) return;" in block   # renderScalePreview
        assert "if (card) card.style.display" in block           # syncScaleCardVisibility
        assert "if (scaleTag) {" in js                           # updateSummary

    def test_the_default_state_sends_nothing(self):
        js = open(os.path.join(ROOT, "website", "order", "order-ui.js"), encoding="utf-8").read()
        assert "scale: 'fit'," in js   # and buildPrintSpec only emits for 'actual'

    def test_a_batched_file_keeps_its_own_choice(self):
        """Staff can queue several files, each with its own options; the batch
        snapshot feeds buildPrintSpec, so dropping scale there would lose the
        choice on every file but the last one — and lose it silently."""
        js = open(os.path.join(ROOT, "website", "order", "order-ui.js"), encoding="utf-8").read()
        snap = js[js.index("function snapshotSpec()"):js.index("function currentRecord()")]
        assert "scale: state.scale," in snap


class TestTheCustomerControl:
    """The customer gets Fit and Actual — two choices that are hard to get wrong.
    Custom % is staff-only (owner, 2026-08-30), so it must not be reachable
    from the order page at all."""

    def order_v2(self):
        return open(os.path.join(ROOT, "website", "order-v2.html"), encoding="utf-8").read()

    def order_ui(self):
        return open(os.path.join(ROOT, "website", "order", "order-ui.js"), encoding="utf-8").read()

    def test_exactly_two_choices(self):
        assert re.findall(r'data-scale="([a-z]+)"', self.order_v2()) == ["fit", "actual"]

    def test_custom_is_not_offered_anywhere_on_the_order_page(self):
        page = self.order_v2()
        assert 'data-scale="custom"' not in page
        assert "Custom %" not in page

    def test_fit_is_the_default(self):
        assert 'class="ov2-tog active" data-scale="fit"' in self.order_v2()
        assert "scale: 'fit'," in self.order_ui()

    def test_the_price_note_is_shown(self):
        assert "Price is per sheet — scaling does not change it." in self.order_v2()

    def test_the_card_hides_on_nup(self):
        """N-up already IS a fit — the planner drops any scale on it and alerts,
        so the customer must not be offered a choice that gets ignored."""
        js = self.order_ui()
        assert "function syncScaleCardVisibility()" in js
        assert "state.nup === 1 ? '' : 'none'" in js

    def test_geometry_comes_from_the_endpoint(self):
        """The whole design: no JS copy of the geometry to drift from what
        actually prints."""
        js = self.order_ui()
        assert "/order/scale-rect?" in js
        assert "sheet_w" in js and "sheet_h" in js

    def test_a_failed_lookup_shows_no_invented_placement(self):
        assert "(preview unavailable)" in self.order_ui()

    def test_superseded_lookups_are_ignored(self):
        js = self.order_ui()
        assert "scaleRectSeq" in js and "if (seq !== scaleRectSeq) return;" in js

    def test_cropping_is_shown_and_named(self):
        assert "the edges will be cut off" in self.order_ui()
        assert ".ov2-scale-page.crops" in self.order_v2()

    def test_the_live_page_is_indexable(self):
        """The trial carried noindex so it could never outrank the real page.
        Folded in, it must not carry that flag onto the page customers land on."""
        page = self.order_v2()
        assert "noindex" not in page
        assert 'canonical" href="https://printosky.com/order-v2.html"' in page

    def test_every_id_it_touches_exists(self):
        page, js = self.order_v2(), self.order_ui()
        start = js.index("// ── Page scaling (Fit / Actual size)")
        block = js[start:js.index("function setDirection", start)]
        for el_id in sorted(set(re.findall(r"\$\('([^']+)'\)", block))):
            assert f'id="{el_id}"' in page, f"$({el_id!r}) has no element in order-v2.html"


class TestTheTwoConsolesAgree:
    """jobs.html and admin.html mirror one panel. Drift between them has bitten
    this repo before, so the scaling blocks are compared directly."""

    def _extract(self, name, start, end):
        page = html(name)
        i = page.index(start)
        return page[i:page.index(end, i)]

    def test_the_control_markup_matches(self):
        a = self._extract("jobs.html", 'id="jp-scale-mode"', "</div>")
        b = self._extract("admin.html", 'id="jp-scale-mode"', "</div>")
        assert a == b

    def test_the_javascript_matches(self):
        a = self._extract("jobs.html", "function setScaleMode(", "// ── Save specs")
        b = self._extract("admin.html", "function setScaleMode(", "// ── Save specs")
        assert a == b

    def test_the_preview_pane_markup_matches(self):
        a = self._extract("jobs.html", '<div class="jp-scale-preview"', 'id="jp-preview-footer"')
        b = self._extract("admin.html", '<div class="jp-scale-preview"', 'id="jp-preview-footer"')
        assert a == b


def test_the_customer_card_uses_the_same_words_as_the_staff_panel():
    """One name for one thing, across all three places it appears.

    The staff panel, the admin mirror and the customer page each label this
    control independently; they drifted into agreement once and could drift out.
    """
    order = open(os.path.join(ROOT, "website", "order-v2.html"), encoding="utf-8").read()
    card = re.search(r'id="ov2-scale-card">\s*<div class="ov2-card-label">([^<]+)</div>', order)
    assert card, "no label on the customer scale card"
    assert card.group(1).strip() == "Scale option"
    for name in CONSOLES:
        assert "Scale option" in html(name)
    # The old wording is gone everywhere, not just renamed in one file.
    assert "Page size on paper" not in order
    for name in CONSOLES:
        assert "Page size on paper" not in html(name)
