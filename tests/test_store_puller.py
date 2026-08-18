"""Unit tests for store_puller — the routed-job downloader on a store PC."""
from __future__ import annotations

import sqlite3

import pytest

from store_puller import (
    ensure_pulled_table,
    load_pulled_ids,
    record_pulled,
    safe_filename,
    select_pullable,
    fetch_assigned_paid,
    pull_once,
    printer_key_for,
    colour_mode_for,
)


# ---------- fakes --------------------------------------------------------------

class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Mimics the supabase-py chained builder: .select().eq().eq().execute()."""

    def __init__(self, rows):
        self._rows = rows
        self._filters: dict[str, str] = {}

    def select(self, _cols):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def execute(self):
        out = [
            r for r in self._rows
            if all(r.get(k) == v for k, v in self._filters.items())
        ]
        return _FakeResult(out)


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _FakeQuery(self._rows)


def _mem_conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


# ---------- safe_filename ------------------------------------------------------

class TestSafeFilename:
    def test_prefixes_job_id(self):
        assert safe_filename("NTK-1", "report.pdf") == "NTK-1__report.pdf"

    def test_sanitises_unsafe_chars(self):
        got = safe_filename("NTK-1", "my report (final).pdf")
        assert got == "NTK-1__my_report__final_.pdf"
        assert "/" not in got and " " not in got

    def test_missing_filename_falls_back(self):
        assert safe_filename("NTK-1", None) == "NTK-1__file"
        assert safe_filename("NTK-1", "   ") == "NTK-1__file"


# ---------- select_pullable ----------------------------------------------------

class TestSelectPullable:
    def _row(self, **kw):
        base = {"job_id": "J1", "status": "Paid", "file_url": "http://x/f.pdf"}
        base.update(kw)
        return base

    def test_happy_row_selected(self):
        rows = [self._row()]
        assert [r["job_id"] for r in select_pullable(rows, set())] == ["J1"]

    def test_excludes_already_pulled(self):
        rows = [self._row(job_id="J1")]
        assert select_pullable(rows, {"J1"}) == []

    def test_excludes_non_paid_status(self):
        rows = [self._row(status="Pending"), self._row(job_id="J2", status="Delivered")]
        assert select_pullable(rows, set()) == []

    def test_excludes_missing_file_url(self):
        assert select_pullable([self._row(file_url="")], set()) == []
        assert select_pullable([self._row(file_url=None)], set()) == []

    def test_excludes_blank_job_id(self):
        assert select_pullable([self._row(job_id="")], set()) == []


# ---------- pulled_jobs tracking table -----------------------------------------

class TestPulledTable:
    def test_roundtrip(self):
        conn = _mem_conn()
        ensure_pulled_table(conn)
        assert load_pulled_ids(conn) == set()
        record_pulled(conn, "J1", "C:/x/J1__f.pdf")
        record_pulled(conn, "J2", "C:/x/J2__g.pdf")
        assert load_pulled_ids(conn) == {"J1", "J2"}

    def test_record_is_idempotent(self):
        conn = _mem_conn()
        record_pulled(conn, "J1", "a")
        record_pulled(conn, "J1", "b")  # INSERT OR REPLACE — no error, no dup
        assert load_pulled_ids(conn) == {"J1"}


# ---------- fetch_assigned_paid ------------------------------------------------

class TestFetchAssignedPaid:
    def test_filters_by_store_and_status(self):
        rows = [
            {"job_id": "A", "assigned_store_id": "NTK", "status": "Paid"},
            {"job_id": "B", "assigned_store_id": "OSP", "status": "Paid"},
            {"job_id": "C", "assigned_store_id": "NTK", "status": "Pending"},
        ]
        got = fetch_assigned_paid(_FakeClient(rows), "NTK")
        assert [r["job_id"] for r in got] == ["A"]

    def test_empty_when_none_match(self):
        assert fetch_assigned_paid(_FakeClient([]), "NTK") == []


# ---------- pull_once (orchestration) ------------------------------------------

class TestPullOnce:
    @pytest.fixture(autouse=True)
    def _claim_granted(self, monkeypatch):
        """These tests predate multi-box coordination and drive a single box.
        Grant every claim so they keep testing what they were written to test;
        TestPrintClaim below covers the claim itself."""
        import store_puller as sp
        monkeypatch.setattr(sp, "_claim", lambda job_id: True)
        monkeypatch.setattr(sp, "_unclaim", lambda job_id: None)

    def _rows(self):
        return [
            {"job_id": "J1", "assigned_store_id": "NTK", "status": "Paid",
             "file_url": "http://x/1.pdf", "filename": "one.pdf"},
            {"job_id": "J2", "assigned_store_id": "NTK", "status": "Paid",
             "file_url": "http://x/2.pdf", "filename": "two.pdf"},
        ]

    def test_downloads_each_new_job_once(self, tmp_path):
        conn = _mem_conn()
        calls = []

        def fake_dl(url, dest):
            calls.append((url, dest))
            return 123

        pulled = pull_once(_FakeClient(self._rows()), "NTK", str(tmp_path),
                           conn, downloader=fake_dl)
        assert set(pulled) == {"J1", "J2"}
        assert len(calls) == 2
        assert load_pulled_ids(conn) == {"J1", "J2"}

    def test_second_pass_pulls_nothing(self, tmp_path):
        conn = _mem_conn()
        dl = lambda url, dest: 1
        pull_once(_FakeClient(self._rows()), "NTK", str(tmp_path), conn, downloader=dl)
        calls = []
        pulled = pull_once(_FakeClient(self._rows()), "NTK", str(tmp_path),
                           conn, downloader=lambda u, d: calls.append(1))
        assert pulled == []
        assert calls == []

    def test_failed_download_not_recorded_and_retries(self, tmp_path):
        conn = _mem_conn()

        def boom(url, dest):
            raise RuntimeError("network down")

        pulled = pull_once(_FakeClient(self._rows()), "NTK", str(tmp_path),
                           conn, downloader=boom)
        assert pulled == []
        assert load_pulled_ids(conn) == set()  # nothing recorded — will retry

        # next pass with a working downloader picks them up
        ok = pull_once(_FakeClient(self._rows()), "NTK", str(tmp_path),
                       conn, downloader=lambda u, d: 1)
        assert set(ok) == {"J1", "J2"}

    def test_only_this_store(self, tmp_path):
        rows = self._rows() + [
            {"job_id": "X", "assigned_store_id": "OSP", "status": "Paid",
             "file_url": "http://x/x.pdf", "filename": "x.pdf"},
        ]
        conn = _mem_conn()
        pulled = pull_once(_FakeClient(rows), "NTK", str(tmp_path),
                           conn, downloader=lambda u, d: 1)
        assert "X" not in pulled
        assert set(pulled) == {"J1", "J2"}

    def test_on_pulled_hook_called_per_job(self, tmp_path):
        conn = _mem_conn()
        seen = []
        pull_once(_FakeClient(self._rows()), "NTK", str(tmp_path), conn,
                  downloader=lambda u, d: 1,
                  on_pulled=lambda row, dest: seen.append((row["job_id"], dest)))
        assert sorted(j for j, _ in seen) == ["J1", "J2"]
        assert all(d.endswith(".pdf") for _, d in seen)

    def test_on_pulled_failure_does_not_break_pull(self, tmp_path):
        conn = _mem_conn()

        def boom(row, dest):
            raise RuntimeError("printer offline")

        # A raising hook must not crash pull_once — but the job must NOT be
        # recorded as pulled, so it retries next cycle instead of stranding.
        pulled = pull_once(_FakeClient(self._rows()), "NTK", str(tmp_path), conn,
                           downloader=lambda u, d: 1, on_pulled=boom)
        assert pulled == []
        assert load_pulled_ids(conn) == set()

    def test_failed_print_retries_next_cycle(self, tmp_path):
        conn = _mem_conn()
        # First cycle: print fails -> not recorded.
        pulled1 = pull_once(_FakeClient(self._rows()), "NTK", str(tmp_path), conn,
                            downloader=lambda u, d: 1, on_pulled=lambda r, d: False)
        assert pulled1 == []
        assert load_pulled_ids(conn) == set()
        # Second cycle: print succeeds -> recorded, not re-pulled after.
        pulled2 = pull_once(_FakeClient(self._rows()), "NTK", str(tmp_path), conn,
                            downloader=lambda u, d: 1, on_pulled=lambda r, d: True)
        assert set(pulled2) == {"J1", "J2"}
        assert load_pulled_ids(conn) == {"J1", "J2"}

    def test_reconcile_resets_stranded_paid_jobs(self, tmp_path):
        from store_puller import reconcile_stranded, record_pulled
        conn = _mem_conn()
        # J1 was pulled-but-not-printed under old code; cloud still says Paid.
        record_pulled(conn, "J1", "x")
        record_pulled(conn, "J9", "y")   # not in the cloud's Paid set → keep
        n = reconcile_stranded(_FakeClient(self._rows()), "NTK", conn)
        assert n == 1
        assert load_pulled_ids(conn) == {"J9"}   # J1 cleared for retry


# ---------- auto-print helpers -------------------------------------------------

class TestPrintClaim:
    """Every PC at a store runs this puller. Without a claim shared between
    them, two boxes see the same paid job and both print it — on paper, in
    front of the customer. `pulled_jobs` is local to a box and cannot help."""

    def _rows(self):
        return [{"job_id": "J1", "filename": "a.pdf", "file_url": "http://x/a.pdf",
                 "status": "Paid", "assigned_store_id": "NTK"},
                {"job_id": "J2", "filename": "b.pdf", "file_url": "http://x/b.pdf",
                 "status": "Paid", "assigned_store_id": "NTK"}]

    def _conn(self):
        conn = sqlite3.connect(":memory:")
        ensure_pulled_table(conn)
        return conn

    def test_an_unclaimed_job_is_skipped_entirely(self, tmp_path, monkeypatch):
        import store_puller as sp
        monkeypatch.setattr(sp, "_claim", lambda job_id: job_id == "J1")
        downloaded = []
        pulled = pull_once(_FakeClient(self._rows()), "NTK", str(tmp_path), self._conn(),
                           downloader=lambda url, dest: downloaded.append(dest) or 10)
        assert pulled == ["J1"]
        assert len(downloaded) == 1, "a job claimed by another box must not even download"

    def test_nothing_prints_when_no_claim_can_be_taken(self, tmp_path, monkeypatch):
        """Fails closed: Supabase unreachable means we cannot know whether
        another box is printing this, so we wait rather than duplicate."""
        import store_puller as sp
        monkeypatch.setattr(sp, "_claim", lambda job_id: False)
        pulled = pull_once(_FakeClient(self._rows()), "NTK", str(tmp_path), self._conn(),
                           downloader=lambda url, dest: 10)
        assert pulled == []

    def test_a_failed_print_hands_the_claim_back(self, tmp_path, monkeypatch):
        import store_puller as sp
        released = []
        monkeypatch.setattr(sp, "_claim", lambda job_id: True)
        monkeypatch.setattr(sp, "_unclaim", lambda job_id: released.append(job_id))
        pulled = pull_once(_FakeClient(self._rows()[:1]), "NTK", str(tmp_path), self._conn(),
                           downloader=lambda url, dest: 10,
                           on_pulled=lambda row, dest: False)     # print failed
        assert pulled == []
        assert released == ["J1"], "an unprinted job must be released for the retry"

    def test_a_failed_download_hands_the_claim_back(self, tmp_path, monkeypatch):
        import store_puller as sp
        released = []
        monkeypatch.setattr(sp, "_claim", lambda job_id: True)
        monkeypatch.setattr(sp, "_unclaim", lambda job_id: released.append(job_id))

        def _boom(url, dest):
            raise OSError("network")

        pull_once(_FakeClient(self._rows()[:1]), "NTK", str(tmp_path), self._conn(),
                  downloader=_boom)
        assert released == ["J1"]

    def test_claim_falls_through_when_coordination_is_not_deployed(self, monkeypatch):
        """An older store PC without device_lease keeps its previous behaviour
        rather than refusing to print."""
        import builtins
        import store_puller as sp
        real_import = builtins.__import__

        def _no_device_lease(name, *a, **k):
            if name == "device_lease":
                raise ImportError("not deployed here")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _no_device_lease)
        assert sp._claim("J1") is True


class TestPrinterRouting:
    def test_printer_key_colour_to_epson(self):
        assert printer_key_for("col") == "epson"
        assert printer_key_for("colour") == "epson"
        assert printer_key_for("COLOR") == "epson"

    def test_printer_key_bw_to_konica(self):
        # konica is redirected to epson by the print server on no-Konica stores
        assert printer_key_for("bw") == "konica"
        assert printer_key_for("mixed") == "konica"
        assert printer_key_for(None) == "konica"

    def test_colour_mode_mapping(self):
        assert colour_mode_for("col") == "colour"
        assert colour_mode_for("bw") == "bw"
        assert colour_mode_for("mixed") == "auto"
        assert colour_mode_for(None) == "auto"


class TestAutoPrintThreading:
    """auto_print must forward paper_size/orientation through to send_to_printer."""

    def test_forwards_paper_and_orientation(self, monkeypatch):
        import print_server
        from store_puller import auto_print

        captured = {}

        def fake_send(job_id, path, printer_key, **kw):
            captured.update(job_id=job_id, printer_key=printer_key, **kw)
            return True, "ok"

        monkeypatch.setattr(print_server, "send_to_printer", fake_send)
        assert auto_print("J1", "f.pdf", "col", 2,
                          paper_size="A3", orientation="landscape") is True
        assert captured["printer_key"] == "epson"
        assert captured["copies"] == 2
        assert captured["colour_mode"] == "colour"
        assert captured["paper_size"] == "A3"
        assert captured["orientation"] == "landscape"

    def test_defaults_none_when_absent(self, monkeypatch):
        import print_server
        from store_puller import auto_print

        captured = {}
        monkeypatch.setattr(print_server, "send_to_printer",
                            lambda *a, **k: (captured.update(k), (True, "ok"))[1])
        auto_print("J2", "f.pdf", "bw", 1)
        assert captured["paper_size"] is None
        assert captured["orientation"] is None


class TestSumatraPaper:
    def test_a_series_uppercased(self):
        from print_server import _sumatra_paper
        assert _sumatra_paper("a4") == "A4"
        assert _sumatra_paper("A3") == "A3"

    def test_named_sizes_lowercased(self):
        from print_server import _sumatra_paper
        assert _sumatra_paper("Legal") == "legal"
        assert _sumatra_paper("Letter") == "letter"

    def test_unknown_or_empty_is_none(self):
        from print_server import _sumatra_paper
        assert _sumatra_paper(None) is None
        assert _sumatra_paper("") is None
        assert _sumatra_paper("A0") is None


class TestEffectivePrinterKey:
    """Shared no-Konica redirect used by BOTH the auto-print and staff paths."""

    def test_konica_redirects_when_ip_empty(self, monkeypatch):
        import print_server
        monkeypatch.setitem(print_server.PRINTER_IPS, "konica", "")
        assert print_server._effective_printer_key("konica", "J1") == "epson"

    def test_konica_redirects_when_ip_none_string(self, monkeypatch):
        import print_server
        monkeypatch.setitem(print_server.PRINTER_IPS, "konica", "None")
        assert print_server._effective_printer_key("konica") == "epson"

    def test_konica_kept_with_real_ip(self, monkeypatch):
        import print_server
        monkeypatch.setitem(print_server.PRINTER_IPS, "konica", "192.168.55.110")
        assert print_server._effective_printer_key("konica") == "konica"

    def test_epson_untouched(self, monkeypatch):
        import print_server
        monkeypatch.setitem(print_server.PRINTER_IPS, "konica", "")
        assert print_server._effective_printer_key("epson") == "epson"


class TestAutoPrintCleanup:
    """The planner's temp dir must be removed even when a sub-job FAILS."""

    def _make_pdf(self, path, pages=4):
        import fitz
        doc = fitz.open()
        for i in range(pages):
            doc.new_page(width=595, height=842).insert_text((72, 72), f"P{i+1}")
        doc.save(path)
        doc.close()

    def test_temp_dir_removed_on_failure(self, tmp_path, monkeypatch):
        import print_server
        from store_puller import auto_print

        dest = str(tmp_path / "JOBX.pdf")
        self._make_pdf(dest, pages=4)

        # Fail every spool so the sub-job loop breaks on the first action.
        monkeypatch.setattr(print_server, "send_to_printer",
                            lambda *a, **k: (False, "boom"))

        # Mixed spec → planner splits into sub-jobs under a temp_<job> dir.
        spec = {"colour_mode": "mixed", "sides": "ss", "copies": 1,
                "paper_size": "A4", "colour_pages": [2]}
        ok = auto_print("JOBX", dest, "mixed", 1, print_spec=spec)

        assert ok is False
        # No leftover temp working dir anywhere under the dest folder.
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith("temp_")]
        assert leftovers == []
        # The original download is untouched for manual printing.
        assert (tmp_path / "JOBX.pdf").exists()
