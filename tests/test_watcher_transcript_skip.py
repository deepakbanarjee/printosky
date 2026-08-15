"""DTP transcript exports must never auto-log as print jobs.

Exporting a finished transcript from the DTP console drops it into the hot
folder. The watcher used to log each one as a fresh Pending job — re-exporting
a file produced duplicates, and the same path would fire a customer quote reply
if the drop carried a sender. Transcripts are deliverables: staff add them to
print deliberately.

The guard must be specific — ordinary customer files still have to flow through
intake untouched — so both directions are asserted here.
"""
import os
import sys
import types

import pytest

# ── stub heavy deps so watcher.py imports (mirrors test_security_bugs.py) ─────
for _mod in ("requests", "dotenv", "db_cloud", "whatsapp_bot",
             "whatsapp_notify", "razorpay_integration"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

if not hasattr(sys.modules["dotenv"], "load_dotenv"):
    sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None

if "watchdog" not in sys.modules:
    _wd = types.ModuleType("watchdog")
    _wd_obs = types.ModuleType("watchdog.observers")
    _wd_obs.Observer = object
    _wd_ev = types.ModuleType("watchdog.events")
    _wd_ev.FileSystemEventHandler = object
    sys.modules["watchdog"] = _wd
    sys.modules["watchdog.observers"] = _wd_obs
    sys.modules["watchdog.events"] = _wd_ev

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import watcher  # noqa: E402


class _IntakeReached(RuntimeError):
    """Raised by the sqlite stub — proves the file was treated as intake."""


@pytest.fixture
def no_db(monkeypatch):
    """Make any DB access explode, so 'was a job created?' is unambiguous."""
    def _boom(*_a, **_k):
        raise _IntakeReached("log_new_file reached the jobs DB")
    monkeypatch.setattr(watcher.sqlite3, "connect", _boom)


@pytest.mark.parametrize("name", [
    "STD_9_-_AT_transcript.docx",
    "Divya_teacher_bio_transcript.txt",
    "UP_STD_7_By_DM_transcript.pdf",
    "DM_STD_8_Unit_1_TRANSCRIPT.DOCX",   # case-insensitive
])
def test_transcript_exports_are_skipped(tmp_path, no_db, name):
    f = tmp_path / name
    f.write_bytes(b"%PDF-fake")
    # Returns cleanly: no job row, no customer message.
    watcher.log_new_file(str(f))


@pytest.mark.parametrize("name", [
    "customer_order.pdf",
    "JEE MAIN PHYSICS QUESTIONS.pdf",
    "walkin_scan.jpg",
])
def test_ordinary_files_still_reach_intake(tmp_path, no_db, name):
    """Positive control: the guard must not swallow real print jobs."""
    f = tmp_path / name
    f.write_bytes(b"%PDF-fake")
    with pytest.raises(_IntakeReached):
        watcher.log_new_file(str(f))


def test_transcript_in_name_but_not_suffix_still_logs(tmp_path, no_db):
    """Only the export suffix is skipped — a customer file that merely mentions
    'transcript' (e.g. a university transcript to print) is real intake."""
    f = tmp_path / "college transcript request.pdf"
    f.write_bytes(b"%PDF-fake")
    with pytest.raises(_IntakeReached):
        watcher.log_new_file(str(f))
