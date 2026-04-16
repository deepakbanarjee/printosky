"""
Tests for konica_csv_importer.py
Covers: parse_konica_date, safe_int (pure logic), import_csv with temp files
"""

import sys
import os
import csv
import sqlite3
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import konica_csv_importer as kci


# ─────────────────────────────────────────────────────────────────────────────
# parse_konica_date
# ─────────────────────────────────────────────────────────────────────────────

class TestParseKonicaDate:
    def test_standard_format(self):
        result = kci.parse_konica_date("16/Mar/2026 9:46:14 AM")
        assert result is not None
        assert "2026-03-16" in result

    def test_pm_time(self):
        result = kci.parse_konica_date("01/Jan/2026 1:30:00 PM")
        assert "2026-01-01" in result
        assert "13:30" in result

    def test_midnight(self):
        result = kci.parse_konica_date("15/Feb/2026 12:00:00 AM")
        assert result is not None

    def test_noon(self):
        result = kci.parse_konica_date("15/Feb/2026 12:00:00 PM")
        assert result is not None

    def test_empty_string_returns_none(self):
        assert kci.parse_konica_date("") is None

    def test_garbage_returns_none(self):
        assert kci.parse_konica_date("not-a-date") is None

    def test_returns_string(self):
        result = kci.parse_konica_date("16/Mar/2026 9:46:14 AM")
        assert isinstance(result, str)

    def test_all_months(self):
        months = [
            "Jan","Feb","Mar","Apr","May","Jun",
            "Jul","Aug","Sep","Oct","Nov","Dec",
        ]
        for i, m in enumerate(months, 1):
            date_str = f"15/{m}/2026 10:00:00 AM"
            result = kci.parse_konica_date(date_str)
            assert result is not None, f"Failed for month {m}"
            assert f"{i:02d}" in result or str(i) in result


# ─────────────────────────────────────────────────────────────────────────────
# safe_int
# ─────────────────────────────────────────────────────────────────────────────

class TestSafeInt:
    def test_integer_string(self):
        assert kci.safe_int("42") == 42

    def test_zero(self):
        assert kci.safe_int("0") == 0

    def test_empty_string(self):
        assert kci.safe_int("") is None

    def test_non_numeric(self):
        assert kci.safe_int("abc") is None

    def test_float_string(self):
        # Floats are not ints — should return None or truncated
        result = kci.safe_int("3.14")
        assert result is None or isinstance(result, int)

    def test_whitespace_string(self):
        assert kci.safe_int("   ") is None

    def test_large_number(self):
        assert kci.safe_int("999999") == 999999

    def test_negative_number(self):
        result = kci.safe_int("-5")
        assert result == -5 or result is None  # implementation choice


# ─────────────────────────────────────────────────────────────────────────────
# import_csv — with real temp files and in-memory DB
# ─────────────────────────────────────────────────────────────────────────────

# Column names matching the actual Konica Bizhub CSV export format
_CSV_HEADERS = [
    "Job Number", "Job Type", "User Name", "File Name", "Result",
    "Number of Pages", "Number of Pages Printed",
    "Number of Monochrome Pages Printed", "Number of Color Pages Printed",
    "Number of Copies Printed", "Job Reception Date", "RIP Start Date",
    "RIP End Date", "Print Start Date", "Print End Date",
    "Paper Size", "Paper Type",
]


def _make_csv(rows: list, tmp_dir: str, filename: str = "test.csv") -> str:
    path = os.path.join(tmp_dir, filename)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_HEADERS,
                                extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            full = {h: "" for h in _CSV_HEADERS}
            full.update(row)
            writer.writerow(full)
    return path


def _sample_row(job_number="12345", pages=10, copies=1):
    """Build a minimal valid Konica CSV row. job_number must be numeric."""
    return {
        "Job Number":                            str(job_number),
        "Job Type":                              "Print",
        "User Name":                             "Priya",
        "File Name":                             "test.pdf",
        "Result":                                "OK",
        "Number of Pages":                       str(pages),
        "Number of Pages Printed":               str(pages),
        "Number of Monochrome Pages Printed":    str(pages),
        "Number of Color Pages Printed":         "0",
        "Number of Copies Printed":              str(copies),
        "Job Reception Date":                    "16/Mar/2026 9:46:14 AM",
        "Print End Date":                        "16/Mar/2026 9:47:00 AM",
        "Paper Size":                            "A4",
        "Paper Type":                            "Plain",
    }


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    kci.init_konica_jobs_table(conn)
    conn.commit()
    conn.close()
    return db_path


class TestImportCsv:
    def test_import_one_row(self, tmp_path, tmp_db):
        csv_path = _make_csv([_sample_row()], str(tmp_path))
        inserted, skipped, errors = kci.import_csv(csv_path, tmp_db)
        assert inserted == 1
        assert skipped == 0
        assert errors == 0

    def test_import_multiple_rows(self, tmp_path, tmp_db):
        rows = [_sample_row(job_number=str(i)) for i in range(1, 6)]
        csv_path = _make_csv(rows, str(tmp_path))
        inserted, skipped, errors = kci.import_csv(csv_path, tmp_db)
        assert inserted == 5

    def test_duplicate_job_number_skipped(self, tmp_path, tmp_db):
        row = _sample_row(job_number="99001")  # numeric job number required
        csv_path = _make_csv([row, row], str(tmp_path))
        inserted, skipped, errors = kci.import_csv(csv_path, tmp_db)
        assert inserted == 1
        assert skipped == 1

    def test_reimport_same_file_all_skipped(self, tmp_path, tmp_db):
        rows = [_sample_row(job_number=str(i)) for i in range(3)]
        csv_path = _make_csv(rows, str(tmp_path))
        kci.import_csv(csv_path, tmp_db)
        inserted2, skipped2, _ = kci.import_csv(csv_path, tmp_db)
        assert inserted2 == 0
        assert skipped2 == 3

    def test_returns_tuple_of_three(self, tmp_path, tmp_db):
        csv_path = _make_csv([_sample_row()], str(tmp_path))
        result = kci.import_csv(csv_path, tmp_db)
        assert len(result) == 3

    def test_data_persisted_to_db(self, tmp_path, tmp_db):
        csv_path = _make_csv([_sample_row(job_number="9901", pages=25)], str(tmp_path))
        kci.import_csv(csv_path, tmp_db)
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT * FROM konica_jobs WHERE job_number=9901").fetchone()
        conn.close()
        assert row is not None
