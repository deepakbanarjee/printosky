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

    def update(self, payload):
        self._log.append((self._table, payload))
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
        return [cols for _t, cols in self.queries if isinstance(cols, str)]

    def updates(self):
        return [payload for _t, payload in self.queries if isinstance(payload, dict)]


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


# ─────────────────────────────────────────────────────────────────────────────
# Fail-loud: a dead transcription job must reach a human
# ─────────────────────────────────────────────────────────────────────────────

def test_a_failed_job_alerts(monkeypatch, tmp_path):
    """Nothing retries a row that reaches `failed` — the loop claims `pending`
    and resumes `transcribing`, never `failed` — and the dtp console shows it
    only to whoever opens it. So the failure itself has to alert
    (docs/FAIL_LOUD.md). Two manuscripts died unnoticed for ten days when the
    worker started writing a `confidence_data` column that did not exist."""
    fake = _install(monkeypatch, tmp_path, rows=[])
    reports = []
    monkeypatch.setattr(worker, "_report_health",
                        lambda name, ok, detail="": reports.append((name, ok, detail)))

    def _boom(_filename):
        raise OSError("404 from storage")

    monkeypatch.setattr(worker, "download_pdf_from_storage", _boom)

    worker.process_transcription_job({"id": "job-1", "filename": "notes.pdf", "mode": "standard"})

    assert {"status": "failed"} in fake.updates(), "the row must still be marked failed"
    assert reports, "a failed transcription job must report to ops_watchdog"
    name, ok, detail = reports[0]
    assert name == "transcription_worker.job"
    assert ok is False
    assert "notes.pdf" in detail


def test_worker_only_touches_columns_that_exist(monkeypatch):
    """The confidence_data outage, as a test.

    tools/cloud_transcription_worker.py started writing `confidence_data` in
    1ee8f25 with no migration behind it. Every page-1 write came back
    "Could not find the 'confidence_data' column ... in the schema cache", the
    worker caught it and set status='failed', and transcription was dead from
    2026-08-18 until someone looked. Any column the worker reads or writes must
    be in the schema manifest — which scripts/check_schema.py then diffs
    against live Supabase.
    """
    import ast
    import pathlib

    import yaml

    root = pathlib.Path(__file__).resolve().parent.parent
    manifest = yaml.safe_load((root / "config" / "schema_manifest.yaml").read_text(encoding="utf-8"))
    known = set(manifest["tables"]["manuscript_transcripts"]["columns"])

    src = (root / "tools" / "cloud_transcription_worker.py").read_text(encoding="utf-8-sig")
    touched: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr == "update":
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    touched |= {k.value for k in arg.keys
                                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        elif node.func.attr == "select":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    touched |= {c.strip() for c in arg.value.split(",") if c.strip() != "*"}

    unknown = touched - known
    assert not unknown, (
        f"the worker reads/writes {sorted(unknown)} on manuscript_transcripts, which the "
        "schema manifest does not have. Ship the migration in the same PR as the code."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Realtime: the subscription #76 did not reach
# ─────────────────────────────────────────────────────────────────────────────

class _FakeChannel:
    def __init__(self, captured):
        self._captured = captured

    def on_postgres_changes(self, event, callback=None, table=None, schema=None, filter=None):
        self._captured.update(event=event, table=table, schema=schema,
                              filter=filter, callback=callback)
        return self

    async def subscribe(self):
        self._captured["subscribed"] = True
        return self


class _FakeAsyncClient:
    def __init__(self, captured):
        self._captured = captured
        self.realtime = self

    async def connect(self):
        self._captured["connected"] = True

    def channel(self, topic):
        self._captured["topic"] = topic
        return _FakeChannel(self._captured)


def _install_async_supabase(monkeypatch, captured, fail=None):
    async def _create_async_client(url, key):
        captured.update(url=url, key=key)
        if fail is not None:
            raise fail
        return _FakeAsyncClient(captured)

    module = types.ModuleType("supabase")
    module.create_async_client = _create_async_client
    # The worker imports create_async_client inside _run, so this fake is what
    # it picks up; the module-level `from supabase import create_client` at
    # import time has already happened.
    monkeypatch.setitem(sys.modules, "supabase", module)
    monkeypatch.setattr(worker, "url", "https://x.supabase.co", raising=False)
    monkeypatch.setattr(worker, "key", "svc", raising=False)


def test_realtime_subscribes_and_wakes_the_poll_loop(monkeypatch):
    """The whole point: a pending row wakes the loop instead of waiting out
    the 900s fallback poll."""
    import threading

    captured = {}
    _install_async_supabase(monkeypatch, captured)
    reports = []
    monkeypatch.setattr(worker, "_report_health",
                        lambda name, ok, detail="": reports.append((name, ok)))
    worker._wake_event.clear()

    stop = threading.Event()
    stop.set()                      # subscribe, report, then return immediately
    worker._realtime_thread(stop)

    assert captured["topic"] == "manuscript-transcripts"
    assert captured["table"] == "manuscript_transcripts"
    assert captured["schema"] == "public"
    assert captured.get("connected") and captured.get("subscribed")
    assert reports == [("transcription_worker.realtime", True)]

    captured["callback"]({"eventType": "INSERT"})
    assert worker._wake_event.is_set(), "a row change must wake the poll loop"
    worker._wake_event.clear()


def test_realtime_failure_alerts_and_leaves_polling_running(monkeypatch):
    """Best-effort by design — a realtime failure must alert, not raise."""
    import threading

    captured = {}
    _install_async_supabase(monkeypatch, captured, fail=OSError("connection refused"))
    reports = []
    monkeypatch.setattr(worker, "_report_health",
                        lambda name, ok, detail="": reports.append((name, ok, detail)))

    worker._realtime_thread(threading.Event())      # must not raise

    assert reports == [("transcription_worker.realtime", False,
                        "OSError: connection refused")]


def test_realtime_without_credentials_alerts(monkeypatch):
    import threading

    reports = []
    monkeypatch.setattr(worker, "_report_health",
                        lambda name, ok, detail="": reports.append((name, ok, detail)))
    monkeypatch.setattr(worker, "url", "", raising=False)
    monkeypatch.setattr(worker, "key", "", raising=False)

    worker._realtime_thread(threading.Event())

    assert reports == [("transcription_worker.realtime", False,
                        "SUPABASE_URL / key not set")]


# ─────────────────────────────────────────────────────────────────────────────
# The day-stamped sync folder must not re-download the whole archive
# ─────────────────────────────────────────────────────────────────────────────

def _counting_fetchers(monkeypatch):
    calls = {"content": 0, "pdf": 0}

    def _content(_job_id):
        calls["content"] += 1
        return "transcribed text"

    def _pdf(_filename):
        calls["pdf"] += 1
        return b"%PDF-"

    monkeypatch.setattr(worker, "_fetch_transcript_content", _content)
    monkeypatch.setattr(worker, "download_pdf_from_storage", _pdf)
    return calls


def test_a_job_synced_on_an_earlier_day_is_not_downloaded_again(monkeypatch, tmp_path):
    """The sync folder is day-stamped (C:\\DTP\\DDMMYY), so on the first cycle
    after midnight every completed job in the store's history looked missing
    and the worker re-fetched the transcript, rebuilt the .docx and
    re-downloaded the PDF for all of them — every day, growing with the
    archive. Realtime made it obvious: adding one manuscript woke the loop and
    re-downloaded the lot."""
    old_day = tmp_path / "180826"
    old_day.mkdir(parents=True)
    (old_day / "notes_transcript.txt").write_text("x", encoding="utf-8")
    (old_day / "notes_transcript.docx").write_text("x", encoding="utf-8")
    (old_day / "notes.pdf").write_bytes(b"%PDF-")

    _install(monkeypatch, tmp_path, rows=[{"id": "job-1", "filename": "notes.pdf"}])
    calls = _counting_fetchers(monkeypatch)

    worker.sync_completed_jobs()

    assert calls == {"content": 0, "pdf": 0}, (
        "a job already on disk under any day folder must not be fetched again"
    )


def test_a_job_never_synced_anywhere_still_lands_in_todays_folder(monkeypatch, tmp_path):
    """The flip side: a genuinely new job — or one that completed while this PC
    was off — is absent everywhere and must still sync, exactly once."""
    from datetime import datetime

    (tmp_path / "180826").mkdir(parents=True)      # an unrelated earlier day

    _install(monkeypatch, tmp_path, rows=[{"id": "job-1", "filename": "notes.pdf"}])
    calls = _counting_fetchers(monkeypatch)

    worker.sync_completed_jobs()

    assert calls == {"content": 1, "pdf": 1}
    today = tmp_path / datetime.now().strftime("%d%m%y")
    assert (today / "notes_transcript.txt").exists()
    assert (today / "notes.pdf").exists()


def test_the_scan_survives_a_missing_dtp_root(monkeypatch, tmp_path):
    """First run on a fresh box: C:\\DTP does not exist yet. That is not an
    error, and it must not stop the sync."""
    root = tmp_path / "nothing-here"
    assert worker._synced_filenames(str(root)) == set()

