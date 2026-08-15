"""Safety tests for tools/storage_cleanup.py.

This script deletes customer files, so the classifier is the part that matters:
a false positive is an unrecoverable data loss. These tests pin the three
exclusion rules (referenced, protected, too-young) and the tier matching.
"""
import datetime as _dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

cleanup = pytest.importorskip("storage_cleanup")

BASE = "https://proj.supabase.co"
ALL_TIERS = ["A", "B", "C"]


def _ago(days: float) -> str:
    ts = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    return ts.isoformat().replace("+00:00", "Z")


def _classify(name, age_days, referenced=frozenset(), tiers=ALL_TIERS, override=None):
    return cleanup.classify(
        name, _ago(age_days), 1234, set(referenced), BASE, tiers, override
    )


# ── exclusion rules ───────────────────────────────────────────────────────────

def test_referenced_file_is_never_deleted():
    """A file a job still points at is kept even when it is ancient."""
    name = "project-builder/old.pdf"
    url = f"{BASE}/storage/v1/object/public/incoming-files/{name}"
    assert _classify(name, 999, referenced={url}) is None


def test_payment_evidence_is_protected():
    """book-payments/ is accounting proof — never swept, at any age."""
    assert _classify("book-payments/receipt.jpg", 999) is None


def test_recent_file_is_kept():
    """A file younger than its tier's minimum age never qualifies."""
    assert _classify("project-builder/new.pdf", 5) is None
    assert _classify("outbound/today.pdf", 2) is None


def test_active_intake_is_kept():
    assert _classify("919000000000_20260814_1200_order.pdf", 1) is None


# ── tier matching ─────────────────────────────────────────────────────────────

def test_tier_a_project_builder_over_90d():
    assert _classify("project-builder/PROJ-2026-001.docx", 120) == "A"


def test_tier_b_outbound_over_30d():
    assert _classify("outbound/sent-media.pdf", 45) == "B"


def test_tier_c_root_intake_over_60d():
    assert _classify("918943232033_20260405_130446_Thesis.pdf", 90) == "C"


def test_tier_c_does_not_match_foldered_objects():
    """Tier C is bucket-root intake only; a folder must match its own tier."""
    assert _classify("some-folder/nested.pdf", 300) is None


def test_tier_filter_limits_scope():
    """--tier A must not sweep outbound files."""
    assert _classify("outbound/sent.pdf", 45, tiers=["A"]) is None
    assert _classify("outbound/sent.pdf", 45, tiers=["B"]) == "B"


def test_min_age_override_applies():
    assert _classify("project-builder/x.docx", 100) == "A"
    assert _classify("project-builder/x.docx", 100, override=365) is None


def test_boundary_age_is_inclusive():
    """Exactly at the threshold qualifies; just under does not."""
    assert _classify("outbound/x.pdf", 30.1) == "B"
    assert _classify("outbound/x.pdf", 29.0) is None


def test_missing_created_at_is_treated_as_new():
    """No timestamp → age 0 → kept, rather than deleted by default."""
    assert cleanup.classify(
        "project-builder/x.docx", None, 10, set(), BASE, ALL_TIERS, None
    ) is None
