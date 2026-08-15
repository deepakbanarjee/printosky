"""Egress-shape tests for tools/cloud_transcription_worker.py.

These lock in the fix for the Supabase egress overage (Aug 2026). The worker's
sync pass runs on every poll cycle, so what it *selects* matters more than what
it does with the result: pulling ``content`` (the full OCR transcript) on every
cycle re-downloaded every completed manuscript ~8.6k times a day and blew the
5 GB/month quota on its own.

The invariant under test: the per-cycle probe never selects ``content``, and the
transcript is fetched only when a local file is actually missing.
"""
import os
import sys
import types
from pathlib import Path

import pytest

# The worker imports Gemini/PyMuPDF/Pillow at module scope; none of that is
# needed to exercise the query shape, so stub whatever is absent. Follows the
# `if _mod not in sys.modules` guard used elsewhere in this suite so a real
# installed package is always preferred.
for _mod in ("fitz", "PIL", "PIL.Image", "google", "google.genai",
             "google.genai.errors"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

if not hasattr(sys.modules["PIL"], "Image"):
    sys.modules["PIL"].Image = sys.modules["PIL.Image"]
if not hasattr(sys.modules["google"], "genai"):
    sys.modules["google"].genai = sys.modules["google.genai"]
if not hasattr(sys.modules["google.genai"], "Client"):
    sys.modules["google.genai"].Client = lambda *a, **k: None
if not hasattr(sys.modules["google.genai.errors"], "APIError"):
    class _APIError(Exception):
        pass
    sys.modules["google.genai.errors"].APIError = _APIError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

worker = pytest.importorskip(
    "cloud_transcription_worker",
    reason="worker module needs store_config/dotenv importable",
)


class _Query:
    """Records the column list of each query the worker builds."""

    def __init__(self, log, table, rows):
        self._log = log
        self._table = table
        self._rows = rows

    def select(self, columns):
        self._log.append((self._table, columns))
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return types.SimpleNamespace(data=self._rows)


class _FakeClient:
    def __init__(self, rows_by_table):
        self.queries = []
        self._rows = rows_by_table

    def table(self, name):
        return _Query(self.queries, name, self._rows.get(name, []))

    def selected_columns(self):
        return [cols for _t, cols in self.queries]


def _install(monkeypatch, tmp_path, rows, store_id="STORE1"):
    fake = _FakeClient({"manuscript_transcripts": rows})
    monkeypatch.setattr(worker, "sb", fake)
    monkeypatch.setattr(worker, "MY_STORE_ID", store_id)
    # Redirect the hardcoded C:\DTP sync root at the tmp dir.
    real_join = os.path.join
    monkeypatch.setattr(
        worker.os.path, "join",
        lambda *p: real_join(str(tmp_path), *p[1:]) if p and p[0] == r"C:\DTP"
        else real_join(*p),
    )
    return fake


def test_probe_never_selects_content(monkeypatch, tmp_path):
    """The per-cycle probe must not pull the transcript blob."""
    fake = _install(monkeypatch, tmp_path, rows=[])

    worker.sync_completed_jobs()

    assert fake.selected_columns() == ["id,filename"], (
        "sync probe must select only id,filename — selecting content here is "
        "what caused the egress overage"
    )
    for _table, cols in fake.queries:
        assert "content" not in cols
        assert cols != "*"


def test_no_content_fetch_when_all_files_present(monkeypatch, tmp_path):
    """Steady state (everything already synced) costs exactly one small query."""
    from datetime import datetime
    day = datetime.now().strftime("%d%m%y")
    sync_dir = tmp_path / day
    sync_dir.mkdir(parents=True)
    (sync_dir / "notes_transcript.txt").write_text("x", encoding="utf-8")
    (sync_dir / "notes_transcript.docx").write_text("x", encoding="utf-8")
    (sync_dir / "notes.pdf").write_bytes(b"%PDF-")

    fake = _install(
        monkeypatch, tmp_path,
        rows=[{"id": "job-1", "filename": "notes.pdf"}],
    )

    worker.sync_completed_jobs()

    assert fake.selected_columns() == ["id,filename"], (
        "a fully-synced job must not trigger a second query for content"
    )


def test_content_fetched_only_when_file_missing(monkeypatch, tmp_path):
    """When a local file is missing the transcript IS fetched — lazily."""
    fake = _install(
        monkeypatch, tmp_path,
        rows=[{"id": "job-1", "filename": "notes.pdf"}],
    )
    fake._rows["manuscript_transcripts"] = [{"id": "job-1", "filename": "notes.pdf"}]

    # _fetch_transcript_content re-queries the same table; return content for it.
    calls = {"n": 0}

    def _counting_fetch(job_id):
        calls["n"] += 1
        return "transcribed text"

    monkeypatch.setattr(worker, "_fetch_transcript_content", _counting_fetch)
    monkeypatch.setattr(worker, "download_pdf_from_storage", lambda f: b"%PDF-")

    worker.sync_completed_jobs()

    assert calls["n"] == 1, "missing local file must trigger exactly one content fetch"


def test_poll_interval_is_not_hot(monkeypatch):
    """Guard against regressing to the 10s hot loop."""
    assert worker.POLL_SECONDS >= 30, (
        f"poll interval {worker.POLL_SECONDS}s is too aggressive for the egress quota"
    )
