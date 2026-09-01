"""
One New Job flow, and a scale you can set while the customer is still there.

Two problems, reported from the counter on 2026-09-01 as "my admin and job panel
are using 2 different systems":

  1. `admin.html`'s "+ New Job" opened its dark 4-step modal. `jobs.html`'s
     opened `order-v2.html?staff=1` — the **customer** order page — even though
     jobs.html already contained that same modal, byte-identical and completely
     unreachable. Staff got a different system depending on which console they
     happened to open.
  2. Scaling could only be set after the job existed, by finding its row and
     opening the panel. Custom % at intake did not exist anywhere.
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


# ── One flow ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", CONSOLES)
def test_new_job_opens_the_modal_in_both_consoles(name):
    src = html(name)
    btn = re.search(r'<button class="nj-btn-new-job"[^>]*>', src).group(0)
    assert 'onclick="openNewJobModal()"' in btn


@pytest.mark.parametrize("name", CONSOLES)
def test_staff_are_no_longer_sent_to_the_customer_order_page(name):
    """order-v2 is the customer's page. It has no Custom % and never will —
    that is decision A10, not an oversight."""
    assert "order-v2.html?staff=1" not in html(name)


def test_the_two_modals_are_still_identical():
    def block(name):
        s = html(name)
        i = s.index('<div class="modal-overlay" id="newjob-modal"')
        return s[i:s.index("<!-- ── Service Modal", i)]
    assert block("jobs.html") == block("admin.html")


# ── Scale at intake ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", CONSOLES)
def test_the_modal_offers_the_same_four_choices_as_the_panel(name):
    src = html(name)
    block = re.search(r'id="nj-scale-mode".*?</select>', src, re.S).group(0)
    assert re.findall(r'<option value="([a-z]*)"', block) == ["", "fit", "actual", "custom"]
    panel = re.search(r'id="jp-scale-mode".*?</select>', src, re.S).group(0)
    assert (re.findall(r'<option value="([a-z]*)"', block)
            == re.findall(r'<option value="([a-z]*)"', panel))


@pytest.mark.parametrize("name", CONSOLES)
def test_the_modal_offers_the_same_presets_as_the_panel(name):
    src = html(name)
    nj = re.search(r'id="nj-scale-custom".*?</div>', src, re.S).group(0)
    jp = re.search(r'id="jp-scale-custom".*?</div>', src, re.S).group(0)
    assert ([int(p) for p in re.findall(r"njSetScalePercent\((\d+)\)", nj)]
            == [int(p) for p in re.findall(r"setScalePercent\((\d+)\)", jp)]
            == [50, 75, 90, 125, 150, 200])


@pytest.mark.parametrize("name", CONSOLES)
def test_the_intake_percent_box_is_bounded_like_the_panels(name):
    box = re.search(r'id="nj-scale-percent"[^>]*', html(name)).group(0)
    assert 'min="25"' in box and 'max="400"' in box


@pytest.mark.parametrize("name", CONSOLES)
def test_intake_defaults_to_no_scaling(name):
    """Opening the modal and saving must not start scaling anything."""
    src = html(name)
    assert 'let njScaleMode    = "";' in src
    assert 'document.getElementById("nj-scale-mode").value = "";' in src


@pytest.mark.parametrize("name", CONSOLES)
def test_a_percent_is_only_sent_for_custom(name):
    src = html(name)
    assert 'scale_percent:   njScaleMode === "custom" ? njScalePercent : null,' in src


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
