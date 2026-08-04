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
