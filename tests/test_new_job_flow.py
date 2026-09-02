"""
One New Job flow — order-v2 staff mode — and a scale you can set at intake.

The history matters, because this file has now been rewritten once for a
decision that was reversed, and the reversal is the more important record.

**2026-09-01.** Reported from the counter as "my admin and job panel are using
2 different systems": `admin.html`'s "+ New Job" opened its dark 4-step modal,
while `jobs.html`'s opened `order-v2.html?staff=1`, even though jobs.html
contained that same modal, byte-identical and completely unreachable. Both
consoles were pointed at the dark modal, and Custom % was added to it.

**2026-09-02, and this is the version that stands.** The owner rejected that
outright: *"I absolutely hated the dark version of the jobs platform. That is
why we created the order v2 version. It is more clear and interactive. I just
want you to add the missing features to v2."*

The consolidation was right and the direction was wrong. Standardising on the
dark modal was argued from how short the wiring was, which is not a reason that
belongs to anyone at the counter. So:

  * both consoles open **order-v2 staff mode**, the page staff actually use;
  * the dark modal is **deleted** from both — 218 lines of markup and 257 of
    wizard JS each — not left unreachable, because an unreachable second
    implementation is exactly how these two drifted apart in the first place;
  * order-v2 gains **Custom %**, staff-only. It had been excluded there on the
    grounds that customers should not pick percentages, which is still true —
    but v2 is also the staff page, so the exclusion had quietly made Custom %
    unreachable from anywhere the owner looked. That is what "I still don't see
    custom scale" was.
  * services and photocopies post to the **Vercel API**, not the store PC, so
    staff can book them off-site (owner, 2026-09-02).
"""

import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import pdf_scaler
import print_server
from test_service_jobs import JOBS_DDL, PRINT_ITEMS_DDL, counter    # noqa: F401

ROOT = os.path.join(os.path.dirname(__file__), "..")
CONSOLES = ("jobs.html", "admin.html")


def html(name):
    return open(os.path.join(ROOT, "website", name), encoding="utf-8").read()


def order_v2():
    return open(os.path.join(ROOT, "website", "order-v2.html"), encoding="utf-8").read()


def order_ui():
    return open(os.path.join(ROOT, "website", "order", "order-ui.js"), encoding="utf-8").read()


# ── One flow, and it is order-v2 ──────────────────────────────────────────────

@pytest.mark.parametrize("name", CONSOLES)
def test_new_job_opens_order_v2_staff_mode(name):
    src = html(name)
    btn = re.search(r'<button class="nj-btn-new-job"[^>]*>', src).group(0)
    assert 'onclick="openNewJob()"' in btn
    shim = re.search(r"function openNewJob\(\) \{(.*?)\n\}", src, re.S).group(1)
    assert "order-v2.html?staff=1" in shim


@pytest.mark.parametrize("name", CONSOLES)
def test_both_consoles_open_the_same_thing(name):
    """The original complaint. Whatever else changes, this must not come back."""
    assert re.search(r"function openNewJob\(\) \{(.*?)\n\}",
                     html("jobs.html"), re.S).group(1) == \
           re.search(r"function openNewJob\(\) \{(.*?)\n\}",
                     html("admin.html"), re.S).group(1)


@pytest.mark.parametrize("name", CONSOLES)
def test_the_dark_modal_is_gone_not_merely_unreachable(name):
    """It was already unreachable in jobs.html once, and sat there rotting until
    someone noticed the two consoles disagreed. Deleted means deleted."""
    src = html(name)
    for symbol in ("newjob-modal", "openNewJobModal", "njSubmit", "njShowStep",
                   "njSetScaleMode", "njSetScalePercent", "nj-scale-mode",
                   "njCurrentStep", "njUploadedFile", "njQuotedAmount"):
        assert symbol not in src, f"{name} still carries {symbol}"


@pytest.mark.parametrize("name", CONSOLES)
def test_no_orphaned_wizard_state_was_left_behind(name):
    """Five `let nj…` declarations survived the first pass of this removal."""
    assert not re.findall(r"\bnj[A-Z]\w*", html(name))


# ── Custom % now lives where staff can reach it ───────────────────────────────

def test_order_v2_offers_all_three_scale_modes():
    assert re.findall(r'data-scale="([a-z]+)"', order_v2()) == ["fit", "actual", "custom"]


def test_custom_is_hidden_until_staff_mode():
    """Still staff-only (owner, 2026-08-30) — the gate moved, the rule did not."""
    page = order_v2()
    tog = re.search(r'<div class="ov2-tog" data-scale="custom"[^>]*>', page).group(0)
    assert 'style="display:none"' in tog
    assert "function syncStaffScale()" in order_ui()


def test_the_percent_box_is_bounded_like_the_panels():
    box = re.search(r'id="ov2-scale-percent"[^>]*', order_v2()).group(0)
    assert f'min="{pdf_scaler.MIN_PERCENT}"' in box
    assert f'max="{pdf_scaler.MAX_PERCENT}"' in box


def test_the_console_bounds_match_pdf_scalers():
    """Two copies of a bound is how they drift. If this fails, change both."""
    js = order_ui()
    assert f"const SCALE_MIN_PERCENT = {pdf_scaler.MIN_PERCENT};" in js
    assert f"const SCALE_MAX_PERCENT = {pdf_scaler.MAX_PERCENT};" in js


def test_a_percent_out_of_range_is_clamped_and_announced():
    """Clamped, never rejected — a typo should print something sane. But the
    clamp is said out loud, so nobody wonders why 900% came out as 400%."""
    js = order_ui()
    fn = re.search(r"function setScalePercent\(raw\) \{(.*?)\n\}", js, re.S).group(1)
    assert "Math.max(SCALE_MIN_PERCENT, Math.min(SCALE_MAX_PERCENT" in fn
    assert "Scaling is limited to" in fn


def test_the_preview_asks_the_printer_for_custom_geometry():
    """No JavaScript copy of the geometry — a preview drawn by different code
    than the printer gets is a preview that can lie."""
    js = order_ui()
    assert "sheet: state.paperSize, mode: state.scale," in js
    assert "if (state.scale === 'custom') p.set('percent'" in js


def test_the_inline_handler_trap_is_avoided():
    """order-ui.js is an ES module, so its functions are not global. An inline
    oninput="" in the HTML would silently never fire — the exact class of
    nothing-happens bug that made Custom % look absent before."""
    assert 'oninput="setScalePercent' not in order_v2()
    assert "pctInput.addEventListener('input'" in order_ui()


# ── The server stores it on the print item ────────────────────────────────────

def test_a_job_created_without_scale_is_byte_for_byte_what_it_always_was(counter):
    r = print_server.handle_create_job({"pages": 10, "amount_collected": 30})
    item = counter.rows("SELECT * FROM print_items WHERE job_id=?", r["job_id"])[0]
    assert item["scale_mode"] is None
    assert item["scale_percent"] is None


def test_fit_and_actual_reach_the_print_item(counter):
    for mode in ("fit", "actual"):
        r = print_server.handle_create_job(
            {"pages": 10, "amount_collected": 30, "scale_mode": mode})
        item = counter.rows("SELECT * FROM print_items WHERE job_id=?", r["job_id"])[0]
        assert item["scale_mode"] == mode
        assert item["scale_percent"] is None


def test_custom_carries_its_percent(counter):
    r = print_server.handle_create_job(
        {"pages": 10, "amount_collected": 30,
         "scale_mode": "custom", "scale_percent": 75})
    item = counter.rows("SELECT * FROM print_items WHERE job_id=?", r["job_id"])[0]
    assert item["scale_mode"] == "custom"
    assert item["scale_percent"] == 75


def test_a_percent_outside_the_range_is_clamped_not_stored_raw(counter):
    r = print_server.handle_create_job(
        {"pages": 10, "amount_collected": 30,
         "scale_mode": "custom", "scale_percent": 900})
    item = counter.rows("SELECT * FROM print_items WHERE job_id=?", r["job_id"])[0]
    assert item["scale_percent"] == pdf_scaler.MAX_PERCENT


def test_an_unknown_mode_is_refused_and_alerts(counter):
    """A mode nothing can bake would print unscaled while the panel claimed
    otherwise — worse than not offering it."""
    r = print_server.handle_create_job(
        {"pages": 10, "amount_collected": 30, "scale_mode": "shrink-a-bit"})
    item = counter.rows("SELECT * FROM print_items WHERE job_id=?", r["job_id"])[0]
    assert item["scale_mode"] is None
    assert any(name == "scale.unknown_mode" for name, _, _ in counter.alerts)


def test_custom_without_a_usable_percent_is_refused_and_alerts(counter):
    r = print_server.handle_create_job(
        {"pages": 10, "amount_collected": 30,
         "scale_mode": "custom", "scale_percent": "quite a lot"})
    item = counter.rows("SELECT * FROM print_items WHERE job_id=?", r["job_id"])[0]
    assert item["scale_mode"] is None
    assert any(name == "scale.unknown_mode" for name, _, _ in counter.alerts)


def test_intake_scale_is_read_only_from_the_body():
    assert print_server._intake_scale({}) == (None, None)
    assert print_server._intake_scale({"scale_mode": ""}) == (None, None)
    assert print_server._intake_scale({"scale_mode": "  FIT "}) == ("fit", None)


def test_a_service_job_still_gets_no_print_item_even_with_a_scale(counter):
    """Scaling is a print concept. A laminate booked here must not grow a print
    item just because someone left the scale dropdown set."""
    r = print_server.handle_create_job(
        {"pages": 10, "amount_collected": 30, "scale_mode": "fit",
         "service_kind": "laminate", "service_meta": {"sheets": 10}})
    assert counter.rows("SELECT * FROM print_items WHERE job_id=?", r["job_id"]) == []
