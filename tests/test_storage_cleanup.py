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


# ── listing ───────────────────────────────────────────────────────────────────

class _FakeStorage:
    """Mimics Supabase storage list(): non-recursive, folder markers have id=None."""

    def __init__(self, tree):
        self._tree = tree  # {prefix: [entry, ...]}

    def from_(self, _bucket):
        return self

    def list(self, path="", options=None):
        options = options or {}
        offset = options.get("offset", 0)
        limit = options.get("limit", 100)
        return self._tree.get(path, [])[offset:offset + limit]


def _file(name):
    return {"name": name, "id": f"id-{name}", "metadata": {"size": 10},
            "created_at": _ago(200)}


def _folder(name):
    return {"name": name, "id": None, "metadata": None, "created_at": None}


def test_list_objects_recurses_into_folders():
    """Regression: the root listing alone hides every foldered object.

    The first run of this script reported 157 objects for a 433-object bucket
    and matched zero project-builder/ or outbound/ files, because the storage
    API never recurses on its own.
    """
    sb = type("SB", (), {})()
    sb.storage = _FakeStorage({
        "": [_file("root_intake.pdf"), _folder("outbound"), _folder("project-builder")],
        "outbound": [_file("sent1.pdf"), _file("sent2.pdf")],
        "project-builder": [_folder("uploads-v2")],
        "project-builder/uploads-v2": [_file("deep.docx")],
    })

    names = sorted(o["name"] for o in cleanup.list_objects(sb))

    assert names == [
        "outbound/sent1.pdf",
        "outbound/sent2.pdf",
        "project-builder/uploads-v2/deep.docx",
        "root_intake.pdf",
    ], "nested objects must be returned with their full path from the bucket root"


def test_list_objects_excludes_folder_markers():
    """A folder marker is a directory, not a deletable object."""
    sb = type("SB", (), {})()
    sb.storage = _FakeStorage({
        "": [_folder("outbound")],
        "outbound": [_file("a.pdf")],
    })

    objects = cleanup.list_objects(sb)

    assert len(objects) == 1
    assert objects[0]["name"] == "outbound/a.pdf"


def test_list_objects_pages_within_a_folder():
    """Folders larger than one page must be fully drained."""
    sb = type("SB", (), {})()
    sb.storage = _FakeStorage({
        "": [_folder("outbound")],
        "outbound": [_file(f"f{i}.pdf") for i in range(250)],
    })

    assert len(cleanup.list_objects(sb)) == 250
