"""
A failed print must not become a spin, and a dead box must not freeze a job.

2026-09-08, OSP. A paid job whose file was a `.docx` reached SumatraPDF, which
tried to parse it as a PDF and exited 1. `pull_once` did the right thing — left
it un-recorded "to retry next poll" — and then the retry did not wait for the
poll. `claim_job()` and `release_job()` UPDATE the same `jobs` row the puller
subscribes to, so the puller's own bookkeeping fired its own realtime callback,
set `_wake_event`, and made `wait(POLL_SECONDS)` return at once. The loop ran
about once a second for the best part of an hour — re-downloading 43 KB and
writing two Supabase updates every second — until the process died holding a
claim that nothing could expire. Two paid jobs were then skipped every five
minutes for a day, with the log blaming "another box".

Three properties, pinned here:
  1. a job that just failed to print is not retried before the next poll;
  2. that failure is ALERTED, not merely logged;
  3. a claim this box left behind in a past life is released at startup.
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import store_puller as sp
from store_puller import (
    ensure_pulled_table,
    pull_once,
    note_print_failure,
    clear_print_failure,
    retry_ready,
    reset_retry_state,
    release_own_stale_claims,
)


ROWS = [{"job_id": "J1", "filename": "a.docx", "file_url": "http://x/a.docx",
         "status": "Paid", "assigned_store_id": "NTK"}]


class _Result:
    def __init__(self, data): self.data = data


class _Query:
    def __init__(self, rows): self._rows, self._f = rows, {}
    def select(self, *_a, **_k): return self
    def eq(self, k, v): self._f[k] = v; return self
    def execute(self):
        return _Result([r for r in self._rows
                        if all(r.get(k) == v for k, v in self._f.items())])


class _Client:
    def __init__(self, rows): self._rows = rows
    def table(self, _n): return _Query(self._rows)


@pytest.fixture(autouse=True)
def _clean():
    reset_retry_state()
    yield
    reset_retry_state()


@pytest.fixture
def granted(monkeypatch):
    """Claims are not what this file is about; grant them and watch the pacing."""
    monkeypatch.setattr(sp, "_claim", lambda job_id: True)
    monkeypatch.setattr(sp, "_unclaim", lambda job_id: None)


def _advance(monkeypatch, seconds: float):
    base = sp._monotonic()
    monkeypatch.setattr(sp, "_monotonic", lambda: base + seconds)


def _conn():
    c = sqlite3.connect(":memory:")
    ensure_pulled_table(c)
    return c


# ── the backoff itself ────────────────────────────────────────────────────────

def test_a_job_that_never_failed_is_always_ready():
    assert retry_ready("J1") is True


def test_a_failed_job_is_held_back_for_at_least_one_poll():
    delay = note_print_failure("J1")
    assert delay >= sp.POLL_SECONDS
    assert retry_ready("J1") is False


def test_the_hold_expires(monkeypatch):
    delay = note_print_failure("J1")
    _advance(monkeypatch, delay + 1)
    assert retry_ready("J1") is True


def test_repeated_failures_back_off_further():
    first = note_print_failure("J1")
    second = note_print_failure("J1")
    third = note_print_failure("J1")
    assert second == first * 2
    assert third == first * 4


def test_the_backoff_is_capped():
    for _ in range(40):
        delay = note_print_failure("J1")
    assert delay == sp._RETRY_BACKOFF_CAP_SECONDS


def test_a_successful_print_clears_the_penalty():
    note_print_failure("J1")
    assert retry_ready("J1") is False
    clear_print_failure("J1")
    assert retry_ready("J1") is True


def test_the_floor_holds_even_if_the_poll_is_tuned_right_down(monkeypatch):
    """STORE_PULLER_POLL_SECONDS is settable. A one-second poll must still not
    licence a one-second retry of a file that cannot print."""
    monkeypatch.setattr(sp, "POLL_SECONDS", 1)
    assert note_print_failure("J1") >= 60


# ── what that means for a poll cycle ──────────────────────────────────────────

def test_an_immediate_second_cycle_does_not_re_download_a_failed_job(tmp_path, granted):
    """The spin, reproduced: two cycles back to back, as the self-inflicted
    realtime wake produced them. The second must be a no-op."""
    conn = _conn()
    downloads = []

    def dl(url, dest):
        downloads.append(dest)
        return 43966

    assert pull_once(_Client(ROWS), "NTK", str(tmp_path), conn,
                     downloader=dl, on_pulled=lambda r, d: False) == []
    assert len(downloads) == 1
    # Woken again immediately by our own claim/unclaim write.
    assert pull_once(_Client(ROWS), "NTK", str(tmp_path), conn,
                     downloader=dl, on_pulled=lambda r, d: False) == []
    assert len(downloads) == 1, "a failed job must not be re-downloaded on a self-wake"


def test_it_is_retried_once_the_poll_interval_has_passed(tmp_path, granted, monkeypatch):
    conn = _conn()
    downloads = []
    dl = lambda url, dest: downloads.append(dest) or 1

    pull_once(_Client(ROWS), "NTK", str(tmp_path), conn,
              downloader=dl, on_pulled=lambda r, d: False)
    _advance(monkeypatch, sp.POLL_SECONDS + 1)
    pulled = pull_once(_Client(ROWS), "NTK", str(tmp_path), conn,
                       downloader=dl, on_pulled=lambda r, d: True)
    assert pulled == ["J1"]
    assert len(downloads) == 2


def test_a_failed_print_raises_an_alert_not_just_a_log_line(tmp_path, granted, monkeypatch):
    """CLAUDE.md's hard rule, on the step it most applies to. Before this, a
    paid job could fail to print every poll for a day in silence."""
    alerts = []
    monkeypatch.setattr(sp, "_report_health",
                        lambda check, ok, detail, **kw: alerts.append((check, ok, detail)))
    pull_once(_Client(ROWS), "NTK", str(tmp_path), _conn(),
              downloader=lambda u, d: 1, on_pulled=lambda r, d: False)
    failed = [a for a in alerts if a[0] == "store_puller.autoprint" and a[1] is False]
    assert failed, f"no alert raised for a paid job that did not print: {alerts}"
    assert "J1" in failed[0][2]


def test_a_print_that_works_reports_recovery(tmp_path, granted, monkeypatch):
    """Otherwise the alert above can never clear itself."""
    alerts = []
    monkeypatch.setattr(sp, "_report_health",
                        lambda check, ok, detail, **kw: alerts.append((check, ok)))
    pull_once(_Client(ROWS), "NTK", str(tmp_path), _conn(),
              downloader=lambda u, d: 1, on_pulled=lambda r, d: True)
    assert ("store_puller.autoprint", True) in alerts


def test_download_only_mode_reports_nothing_about_printing(tmp_path, granted, monkeypatch):
    """STORE_PULLER_AUTOPRINT=0 means staff print by hand — a green auto-print
    light there would be computed over nothing."""
    alerts = []
    monkeypatch.setattr(sp, "_report_health",
                        lambda check, ok, detail, **kw: alerts.append((check, ok)))
    pull_once(_Client(ROWS), "NTK", str(tmp_path), _conn(), downloader=lambda u, d: 1)
    assert not [a for a in alerts if a[0] == "store_puller.autoprint"]


# ── a file that can never print is not a file to retry ────────────────────────

def test_an_unprintable_job_is_set_aside_permanently():
    sp.mark_unprintable("J1", "no converter for .psd")
    assert retry_ready("J1") is False


def test_no_amount_of_waiting_makes_an_unprintable_job_ready(monkeypatch):
    """The distinction that cost the day: a backoff expires, this does not."""
    sp.mark_unprintable("J1", "no Word on this box")
    _advance(monkeypatch, 60 * 60 * 24 * 7)
    assert retry_ready("J1") is False


def test_a_restart_gives_it_one_more_chance():
    """In memory only, deliberately: the box may have gained Office, or the
    customer may have re-sent the file as a PDF."""
    sp.mark_unprintable("J1", "no Word on this box")
    reset_retry_state()
    assert retry_ready("J1") is True


def test_an_unprintable_job_alerts_differently_from_a_printer_that_is_busy(
        tmp_path, granted, monkeypatch):
    """Two different jobs for whoever reads the alert: one waits, one has to be
    printed by hand or sent back to the customer."""
    alerts = []
    monkeypatch.setattr(sp, "_report_health",
                        lambda check, ok, detail, **kw: alerts.append((check, ok, detail)))

    def on_pulled(row, dest):
        sp.mark_unprintable(row["job_id"], "no converter for .psd —")
        return False

    pull_once(_Client(ROWS), "NTK", str(tmp_path), _conn(),
              downloader=lambda u, d: 1, on_pulled=on_pulled)
    kinds = {a[0] for a in alerts}
    assert "store_puller.unprintable" in kinds
    assert "store_puller.autoprint" not in kinds, (
        "a file that will never print must not be reported as a retryable failure"
    )
    detail = [a for a in alerts if a[0] == "store_puller.unprintable"][0][2]
    assert "will not be retried" in detail
    assert "a.docx" in detail, "the alert must name the file someone has to go and print"


def test_the_docx_that_started_this_converts_or_fails_loudly(tmp_path, monkeypatch):
    """The OSP file itself, through auto_print, on a box with no Word: it must
    come back False having been MARKED, not left to retry every poll."""
    import print_server
    from store_puller import auto_print

    src = tmp_path / "nithya coverpage.docx"
    src.write_bytes(b"PK\x03\x04 a real docx starts like this")
    monkeypatch.setitem(sys.modules, "win32com.client", None)
    monkeypatch.setattr(print_server, "send_to_printer",
                        lambda *a, **k: pytest.fail("SumatraPDF must never see a .docx"))

    assert auto_print("OSP-J", str(src), "bw", 1,
                      print_spec={"sides": "simplex"}) is False
    reason = sp.unprintable_reason("OSP-J")
    assert reason and "Word" in reason


def test_a_photo_reaches_the_printer_as_a_pdf(tmp_path, monkeypatch):
    """The other OSP job. A .jpg must arrive at send_to_printer as a PDF."""
    fitz = pytest.importorskip("fitz")
    import print_server
    from store_puller import auto_print

    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.draw_rect(fitz.Rect(10, 10, 590, 790), fill=(0.1, 0.1, 0.1))
    page.get_pixmap().save(str(tmp_path / "photo.png"))
    doc.close()

    seen = {}
    monkeypatch.setattr(print_server, "send_to_printer",
                        lambda job_id, path, key, **kw: (seen.update(path=path), (True, "ok"))[1])
    assert auto_print("OSP-P", str(tmp_path / "photo.png"), "bw", 1,
                      print_spec={"sides": "simplex"}) is True
    assert seen["path"].lower().endswith(".pdf")


# ── the claim this box left behind ────────────────────────────────────────────

def test_our_own_leftover_claim_is_released_at_startup(monkeypatch):
    released = []
    monkeypatch.setattr(sp, "_report_health", lambda *a, **k: None)
    import device_lease
    monkeypatch.setattr(device_lease, "device_id", lambda: "box-A")
    monkeypatch.setattr(device_lease, "release_job", lambda jid: released.append(jid))
    rows = [{"job_id": "J1", "print_claimed_by": "box-A"},
            {"job_id": "J2", "print_claimed_by": "box-B"},
            {"job_id": "J3", "print_claimed_by": None}]
    assert release_own_stale_claims(rows, "NTK") == ["J1"]
    assert released == ["J1"], "only our own claim is ours to free"


def test_releasing_our_own_leftover_claim_alerts(monkeypatch):
    """A claim held across a restart means the previous run died mid-print. That
    is worth saying out loud — it is how a day was lost."""
    alerts = []
    monkeypatch.setattr(sp, "_report_health",
                        lambda check, ok, detail, **kw: alerts.append((check, ok, detail)))
    import device_lease
    monkeypatch.setattr(device_lease, "device_id", lambda: "box-A")
    monkeypatch.setattr(device_lease, "release_job", lambda jid: None)
    release_own_stale_claims([{"job_id": "J1", "print_claimed_by": "box-A"}], "NTK")
    bad = [a for a in alerts if a[0] == "store_puller.stale_claim" and a[1] is False]
    assert bad and "J1" in bad[0][2]


def test_a_clean_start_reports_clean(monkeypatch):
    alerts = []
    monkeypatch.setattr(sp, "_report_health",
                        lambda check, ok, detail, **kw: alerts.append((check, ok)))
    import device_lease
    monkeypatch.setattr(device_lease, "device_id", lambda: "box-A")
    release_own_stale_claims([{"job_id": "J1", "print_claimed_by": "box-B"}], "NTK")
    assert ("store_puller.stale_claim", True) in alerts


def test_reconcile_asks_the_cloud_for_the_claim_columns():
    """release_own_stale_claims can only see a holder the query actually
    selected. Dropping these columns would turn the recovery into a no-op that
    reports 'no claims left over' — a green light computed over nothing."""
    assert "print_claimed_by" in sp._JOB_COLUMNS
    assert "print_claimed_at" in sp._JOB_COLUMNS
