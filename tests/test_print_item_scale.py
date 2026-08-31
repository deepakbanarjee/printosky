"""
Scaling on the staff manual-print path — A-3 of the scaling plan.

`handle_print_item` builds its own SumatraPDF command from the print_items row
rather than going through print_planner, so it needs its own wiring and its own
guard: a row with no scale_mode must produce exactly the command it always did.

The columns are additive and nullable, so every row written before 2026-08-30
means "no scaling" — and rows on a store PC that has not run the migration yet
have no such columns at all, which is why the handler reads them defensively.
"""

import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fitz
import pytest

import print_server
from nup_imposer import portrait_sheet

A4_W, A4_H = portrait_sheet("A4")
A5_W, A5_H = portrait_sheet("A5")


def _make_pdf(path, w=A5_W, h=A5_H, pages=2):
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=w, height=h).draw_rect(fitz.Rect(5, 5, w - 5, h - 5))
    doc.save(str(path))
    doc.close()


@pytest.fixture
def store(monkeypatch, tmp_path):
    """A store PC with one job, one print item, and a captured printer."""
    db = tmp_path / "jobs.db"
    pdf = tmp_path / "job.pdf"
    _make_pdf(pdf)

    conn = sqlite3.connect(db)
    # The columns handle_print_item touches, including its post-print bookkeeping.
    conn.execute("""CREATE TABLE jobs (job_id TEXT PRIMARY KEY, filepath TEXT,
                    filename TEXT, page_count INTEGER, size TEXT, colour TEXT,
                    status TEXT, printed_by TEXT, notes TEXT)""")
    conn.execute("""CREATE TABLE print_items (job_id TEXT, item_number INTEGER,
                    page_list TEXT, paper_type TEXT, colour TEXT, sides TEXT,
                    layout TEXT, copies INTEGER, paper_gsm INTEGER, printer TEXT,
                    status TEXT, printed_at TEXT, printed_by TEXT,
                    scale_mode TEXT, scale_percent INTEGER)""")
    conn.execute("INSERT INTO jobs VALUES ('OSP-1',?,'job.pdf',2,'A4','bw','Queued',NULL,NULL)",
                 (str(pdf),))
    conn.commit()
    conn.close()

    calls = []

    class Result:
        returncode = 0
        stdout = stderr = ""

    def fake_run(cmd, **kw):
        # Snapshot the file as SumatraPDF would see it — the handler deletes it.
        target = os.path.join(kw.get("cwd", ""), cmd[-1])
        calls.append({"cmd": cmd, "cwd": kw.get("cwd"),
                      "settings": cmd[cmd.index("-print-settings") + 1],
                      "target": target,
                      "pages": _page_sizes(target)})
        return Result()

    def _page_sizes(path):
        doc = fitz.open(path)
        try:
            return [(round(p.rect.width), round(p.rect.height)) for p in doc]
        finally:
            doc.close()

    monkeypatch.setattr(print_server, "DB_PATH", str(db))
    monkeypatch.setattr(print_server, "find_sumatra", lambda: r"C:\SumatraPDF.exe")
    monkeypatch.setattr(print_server, "PRINTERS", {"konica": "KONICA-Q", "epson": "EPSON-Q"})
    monkeypatch.setattr(print_server, "PRINTER_IPS", {"konica": "10.0.0.1"})
    monkeypatch.setattr(print_server, "_write_epson_spec_row", lambda *a, **k: None)
    monkeypatch.setattr(subprocess, "run", fake_run)

    def add_item(**over):
        row = dict(page_list="all", paper_type="A4_BW", colour="bw", sides="ss",
                   layout="1-up", copies=1, paper_gsm=70, printer="konica",
                   scale_mode=None, scale_percent=None)
        row.update(over)
        c = sqlite3.connect(db)
        c.execute("""INSERT INTO print_items
                     (job_id,item_number,page_list,paper_type,colour,sides,layout,
                      copies,paper_gsm,printer,status,scale_mode,scale_percent)
                     VALUES ('OSP-1',1,?,?,?,?,?,?,?,?,'Pending',?,?)""",
                  (row["page_list"], row["paper_type"], row["colour"], row["sides"],
                   row["layout"], row["copies"], row["paper_gsm"], row["printer"],
                   row["scale_mode"], row["scale_percent"]))
        c.commit()
        c.close()

    return {"add_item": add_item, "calls": calls, "db": str(db), "tmp": tmp_path}


class TestWithoutScaling:
    """Every existing row. The command must not have changed."""

    def test_no_scale_columns_set(self, store):
        store["add_item"]()
        assert print_server.handle_print_item("OSP-1", 1)["ok"]
        call = store["calls"][0]
        assert "noscale" not in call["settings"]
        assert call["cmd"][-1] == "job.pdf"          # the original file, by name
        assert call["pages"] == [(round(A5_W), round(A5_H))] * 2

    def test_a_row_predating_the_migration(self, store):
        """A store PC that has not run fix_db.py yet has no such columns."""
        c = sqlite3.connect(store["db"])
        c.execute("ALTER TABLE print_items RENAME TO print_items_new")
        c.execute("""CREATE TABLE print_items (job_id TEXT, item_number INTEGER,
                     page_list TEXT, paper_type TEXT, colour TEXT, sides TEXT,
                     layout TEXT, copies INTEGER, paper_gsm INTEGER, printer TEXT,
                     status TEXT, printed_at TEXT, printed_by TEXT)""")
        c.execute("""INSERT INTO print_items VALUES
                     ('OSP-1',1,'all','A4_BW','bw','ss','1-up',1,70,'konica',
                      'Pending',NULL,NULL)""")
        c.commit()
        c.close()
        assert print_server.handle_print_item("OSP-1", 1)["ok"]
        assert "noscale" not in store["calls"][0]["settings"]


class TestWithScaling:

    @pytest.mark.parametrize("mode,percent", [("fit", None), ("actual", None),
                                              ("custom", 50), ("custom", 150)])
    def test_the_printed_file_is_the_baked_one(self, mode, percent, store):
        store["add_item"](scale_mode=mode, scale_percent=percent)
        assert print_server.handle_print_item("OSP-1", 1)["ok"]
        call = store["calls"][0]
        assert "noscale" in call["settings"]
        assert call["cmd"][-1] != "job.pdf"
        # Baked onto the job's sheet size, whatever the source page was.
        assert call["pages"] == [(round(A4_W), round(A4_H))] * 2

    def test_the_temp_file_is_cleaned_up(self, store):
        store["add_item"](scale_mode="fit")
        print_server.handle_print_item("OSP-1", 1)
        assert not os.path.exists(store["calls"][0]["target"])

    def test_the_original_file_is_untouched(self, store):
        original = (store["tmp"] / "job.pdf").read_bytes()
        store["add_item"](scale_mode="custom", scale_percent=200)
        print_server.handle_print_item("OSP-1", 1)
        assert (store["tmp"] / "job.pdf").read_bytes() == original

    def test_actual_on_a_matching_page_stays_a_no_op(self, store):
        """Nothing to bake, so nothing is baked and no guard token is sent."""
        _make_pdf(store["tmp"] / "job.pdf", A4_W, A4_H)
        store["add_item"](scale_mode="actual")
        assert print_server.handle_print_item("OSP-1", 1)["ok"]
        assert "noscale" not in store["calls"][0]["settings"]

    def test_a_scaling_failure_still_prints_and_alerts(self, store, monkeypatch):
        reported = []
        monkeypatch.setattr(print_server, "_report_health",
                            lambda c, ok, d: reported.append((c, ok)))
        import pdf_scaler
        monkeypatch.setattr(pdf_scaler, "apply_scale",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        store["add_item"](scale_mode="fit")
        assert print_server.handle_print_item("OSP-1", 1)["ok"]
        assert "noscale" not in store["calls"][0]["settings"]
        assert ("print_server.item_scale_failed", False) in reported


class TestSelfMigration:
    """Store PCs update by pulling code and restarting the watcher — nothing
    runs fix_db.py for them. So the code has to work against a database that
    predates the columns, or saving specs breaks at the counter."""

    def test_an_unmigrated_db_gains_the_columns_on_save(self, store, monkeypatch):
        c = sqlite3.connect(store["db"])
        c.execute("DROP TABLE print_items")
        c.execute("""CREATE TABLE print_items (job_id TEXT, item_number INTEGER,
                     page_list TEXT, paper_type TEXT, colour TEXT, sides TEXT,
                     layout TEXT, copies INTEGER, paper_gsm INTEGER, printer TEXT,
                     status TEXT, printed_at TEXT, printed_by TEXT)""")
        for col in ("finishing TEXT", "is_student INTEGER", "urgent INTEGER",
                    "paper_size TEXT", "amount_quoted REAL"):
            c.execute(f"ALTER TABLE jobs ADD COLUMN {col}")
        c.commit()
        c.close()

        monkeypatch.setattr(print_server, "DB_PATH", store["db"])
        r = print_server.handle_update_job(
            {"job_id": "OSP-1",
             "print_items": [{"item_number": 1, "scale_mode": "custom",
                              "scale_percent": 75}]})
        assert r["ok"]

        c = sqlite3.connect(store["db"])
        cols = {row[1] for row in c.execute("PRAGMA table_info(print_items)")}
        saved = c.execute("SELECT scale_mode, scale_percent FROM print_items").fetchone()
        c.close()
        assert {"scale_mode", "scale_percent"} <= cols
        assert saved == ("custom", 75)


class TestUpdateJobPersistsScale:

    def _saved(self, monkeypatch, db, item):
        monkeypatch.setattr(print_server, "DB_PATH", db)
        c = sqlite3.connect(db)
        for col in ("finishing TEXT", "is_student INTEGER", "urgent INTEGER",
                    "paper_size TEXT", "amount_quoted REAL"):
            c.execute(f"ALTER TABLE jobs ADD COLUMN {col}")
        c.commit()
        c.close()
        print_server.handle_update_job({"job_id": "OSP-1", "print_items": [item]})
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT scale_mode, scale_percent FROM print_items").fetchone()
        c.close()
        return row["scale_mode"], row["scale_percent"]

    def test_a_panel_that_sends_nothing_leaves_them_null(self, store, monkeypatch):
        assert self._saved(monkeypatch, store["db"], {"item_number": 1}) == (None, None)

    def test_custom_is_stored_with_its_percent(self, store, monkeypatch):
        assert self._saved(monkeypatch, store["db"],
                           {"item_number": 1, "scale_mode": "custom",
                            "scale_percent": 75}) == ("custom", 75)

    def test_an_out_of_range_percent_is_clamped(self, store, monkeypatch):
        mode, pct = self._saved(monkeypatch, store["db"],
                                {"item_number": 1, "scale_mode": "custom",
                                 "scale_percent": 5000})
        assert (mode, pct) == ("custom", 400)

    def test_a_custom_without_a_usable_percent_is_stored_as_no_scale(self, store, monkeypatch):
        assert self._saved(monkeypatch, store["db"],
                           {"item_number": 1, "scale_mode": "custom",
                            "scale_percent": "nonsense"}) == (None, None)

    def test_a_junk_mode_is_rejected(self, store, monkeypatch):
        assert self._saved(monkeypatch, store["db"],
                           {"item_number": 1, "scale_mode": "shrink"}) == (None, None)
