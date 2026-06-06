"""Tests for academic_pipeline_worker.py — the store-PC worker that polls
Supabase for academic orders in *_generating status, runs the osp-academics
pipeline, uploads the DOCX, advances status, and notifies via WhatsApp.

Had zero coverage. Characterization reading surfaced three bugs, each pinned
by a test below:

  Bug A — a notify failure AFTER a successful upload+status-advance reverts the
          order, undoing a completed generation.
  Bug B — storage upload is not idempotent (no upsert): a retry of an
          already-uploaded project fails forever.
  Bug C — project_id/status are read before the try in _process, so one
          malformed order aborts the entire poll batch.

Run: pytest tests/test_academic_pipeline_worker.py -v
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import academic_pipeline_worker as apw  # noqa: E402


# ── Fake Supabase client ──────────────────────────────────────────────────────
class _Chain:
    """Chainable stand-in for the supabase-py query builder."""
    def __init__(self, store):
        self.store = store
        self._payload = None
        self._select = False

    def update(self, payload):
        self._payload = payload
        return self

    def select(self, *a, **k):
        self._select = True
        return self

    def eq(self, col, val):
        return self

    def in_(self, col, vals):
        return self

    def execute(self):
        if self._select:
            return MagicMock(data=list(self.store["orders"]))
        self.store["status_updates"].append(dict(self._payload))
        return MagicMock(data=[{}])


class _Storage:
    def __init__(self, store):
        self.store = store

    def from_(self, bucket):
        return self

    def upload(self, name, content, file_options=None):
        self.store["uploads"].append({"name": name, "file_options": file_options})
        if self.store.get("upload_raises"):
            raise RuntimeError("Duplicate object name")
        return MagicMock()

    def get_public_url(self, name):
        return f"https://sb.example/{name}"


class FakeClient:
    def __init__(self, store):
        self.store = store
        self.storage = _Storage(store)

    def table(self, name):
        return _Chain(self.store)


def _new_store(orders=None):
    return {"orders": orders or [], "status_updates": [], "uploads": []}


def _good_order(status="chapters_generating"):
    return {
        "project_id": "PROJ-2026-001",
        "status": status,
        "whatsapp_phone": "919495706405",
        "customer_name": "Aswathy",
    }


def _install(monkeypatch, store, tmp_path, *, pipeline_success=True,
             output_exists=True, notify_raises=False):
    """Wire up _client + the lazily-imported pipeline/whatsapp modules."""
    monkeypatch.setattr(apw, "_client", lambda: FakeClient(store))

    # Build a real output file so open()/os.path.exists work for real.
    out = tmp_path / "out.docx"
    out.write_bytes(b"PK\x03\x04 fake docx")
    output_path = str(out) if output_exists else str(tmp_path / "missing.docx")

    pipeline = MagicMock()
    pipeline.build_phase1_brief.return_value = {"brief": 1}
    pipeline.build_phase2_brief.return_value = {"brief": 2}
    pipeline.write_brief.return_value = None
    pipeline.run_pipeline.return_value = {"success": pipeline_success, "error": "boom"}
    pipeline.get_output_path.return_value = output_path

    whatsapp = MagicMock()
    if notify_raises:
        whatsapp.notify_chapters_ready.side_effect = RuntimeError("WA down")
        whatsapp.notify_phase2_link.side_effect = RuntimeError("WA down")

    return patch.dict(sys.modules, {
        "academic_pipeline": pipeline,
        "academic_whatsapp": whatsapp,
    })


def _final_status(store):
    return store["status_updates"][-1]["status"] if store["status_updates"] else None


# ── Happy path ────────────────────────────────────────────────────────────────
class TestProcessSuccess:
    def test_phase1_success_advances_to_chapters_qc(self, monkeypatch, tmp_path):
        store = _new_store()
        with _install(monkeypatch, store, tmp_path):
            apw._process(_good_order("chapters_generating"))
        assert _final_status(store) == "chapters_qc"
        assert store["uploads"][0]["name"] == "PROJ-2026-001-phase1.docx"

    def test_phase2_success_advances_to_final_qc(self, monkeypatch, tmp_path):
        store = _new_store()
        with _install(monkeypatch, store, tmp_path):
            apw._process(_good_order("final_generating"))
        assert _final_status(store) == "final_qc"


# ── Failure handling ──────────────────────────────────────────────────────────
class TestProcessFailureReverts:
    def test_pipeline_failure_reverts_to_advance_paid(self, monkeypatch, tmp_path):
        store = _new_store()
        with _install(monkeypatch, store, tmp_path, pipeline_success=False):
            apw._process(_good_order("chapters_generating"))
        assert _final_status(store) == "advance_paid"

    def test_missing_output_file_reverts(self, monkeypatch, tmp_path):
        store = _new_store()
        with _install(monkeypatch, store, tmp_path, output_exists=False):
            apw._process(_good_order("chapters_generating"))
        assert _final_status(store) == "advance_paid"


# ── Bug A: notify failure must NOT revert a completed order ────────────────────
class TestNotifyFailureDoesNotRevert:
    def test_notify_failure_keeps_completed_status(self, monkeypatch, tmp_path):
        store = _new_store()
        with _install(monkeypatch, store, tmp_path, notify_raises=True):
            apw._process(_good_order("chapters_generating"))
        # The DOCX uploaded and status advanced — a flaky WhatsApp send must not
        # undo that. Final status must remain chapters_qc, not be reverted.
        assert _final_status(store) == "chapters_qc"


# ── Bug B: upload must be idempotent (upsert) so retries don't poison ──────────
class TestUploadIsIdempotent:
    def test_upload_uses_upsert(self, monkeypatch, tmp_path):
        store = _new_store()
        with _install(monkeypatch, store, tmp_path):
            apw._process(_good_order("chapters_generating"))
        opts = store["uploads"][0]["file_options"] or {}
        # upsert must be enabled so re-processing an already-uploaded project
        # overwrites instead of erroring out.
        assert str(opts.get("upsert")).lower() == "true"


# ── Bug C: one malformed order must not abort the whole poll batch ─────────────
class TestPollBatchResilience:
    def test_malformed_order_does_not_skip_later_orders(self, monkeypatch, tmp_path):
        bad = {"status": "chapters_generating"}          # missing project_id
        good = _good_order("chapters_generating")
        store = _new_store(orders=[bad, good])
        with _install(monkeypatch, store, tmp_path):
            apw.poll_once()
        # The good order must still have been processed despite the bad one.
        assert any(u["name"] == "PROJ-2026-001-phase1.docx" for u in store["uploads"]), \
            "later order was skipped because a malformed order aborted the batch"
