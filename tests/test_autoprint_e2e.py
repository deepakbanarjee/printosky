import os
import sqlite3
import pytest
import fitz  # PyMuPDF
from store_puller import pull_once, auto_print
import print_server

class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "jobs"
        return self

    def select(self, cols):
        return self

    def eq(self, col, val):
        return self

    def execute(self):
        class Res:
            def __init__(self, data):
                self.data = data
        return Res(self.rows)

@pytest.fixture
def dummy_6page_pdf(tmp_path):
    pdf_path = os.path.join(tmp_path, "input.pdf")
    doc = fitz.open()
    for i in range(6):
        page = doc.new_page(width=595.28, height=841.89)  # A4 Portrait
        page.insert_text((50, 50), f"Logical Page {i+1}")
    doc.save(pdf_path)
    doc.close()
    return pdf_path

def test_full_pipeline_e2e(dummy_6page_pdf, tmp_path, monkeypatch):
    """End-to-end verification of the auto-print pipeline:
    1. A job with a complex print_spec is pulled.
    2. The downloader copies the dummy 6-page PDF.
    3. The auto_print hook runs the planner.
    4. The planner slices, imposes (2-up), and splits the job (duplex mixed-color).
    5. SumatraPDF print actions are triggered via send_to_printer.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE pulled_jobs (
            job_id     TEXT PRIMARY KEY,
            pulled_at  TEXT NOT NULL,
            dest_path  TEXT
        )
        """
    )
    conn.commit()

    # Spec definition: 2-up, duplex, mixed-colour, logical page 1 is colour.
    spec = {
        "nup": 2,
        "colour_mode": "mixed",
        "colour_pages": [1],  # 1-based index (logical page 1)
        "sides": "duplex",
        "copies": 3,
        "paper_size": "A4",
        "orientation": "portrait"
    }

    job_row = {
        "job_id": "OSP-E2E-TEST",
        "filename": "e2e_input.pdf",
        "file_url": "http://example.com/e2e_input.pdf",
        "status": "Paid",
        "assigned_store_id": "NTK",
        "colour": "mixed",
        "copies": 3,
        "size": "A4",
        "orientation": "portrait",
        "print_spec": spec
    }

    client = FakeClient([job_row])

    # Downloader copies the dummy PDF
    def fake_dl(url, dest):
        import shutil
        shutil.copy2(dummy_6page_pdf, dest)
        return os.path.getsize(dest)

    # Capture calls to send_to_printer
    captured_calls = []
    def fake_send(job_id, filepath, printer_key, **kw):
        captured_calls.append({
            "job_id": job_id,
            "filepath": filepath,
            "printer_key": printer_key,
            **kw
        })
        return True, "spooled"

    monkeypatch.setattr(print_server, "send_to_printer", fake_send)
    monkeypatch.setitem(print_server.PRINTER_IPS, "konica", "192.168.1.100")
    monkeypatch.setitem(print_server.PRINTERS, "konica", "KONICA MINOLTA 1100 PS")

    import print_planner
    monkeypatch.setattr(print_planner, "cleanup_temp_dir", lambda path: None)

    # Auto-print hook configuration
    def on_pulled(row, dest):
        auto_print(
            row.get("job_id"), dest, row.get("colour"), row.get("copies"),
            paper_size=row.get("size"), orientation=row.get("orientation"),
            print_spec=row.get("print_spec"),
        )

    # Run the E2E poll cycle
    pulled = pull_once(
        client=client,
        store_id="NTK",
        dest_dir=str(tmp_path / "Assigned"),
        conn=conn,
        downloader=fake_dl,
        on_pulled=on_pulled
    )

    assert pulled == ["OSP-E2E-TEST"]
    
    # We expect 2 separate print spools (one Colour, one B&W)
    # The 6 logical pages in 2-up duplex:
    #   - Sheet 1 front (imposed page 1) contains logical pages 0 and 1.
    #   - Sheet 1 back (imposed page 2) contains logical pages 2 and 3.
    #   - Sheet 2 front (imposed page 3) contains logical pages 4 and 5.
    #   - Sheet 2 back (imposed page 4) contains logical pages -1 and -1 (blank padding).
    # Total imposed sheets: 2 (4 pages in output PDF).
    #
    # Since logical page 1 (0-based page 0) is colour, Sheet 1 is Colour.
    # Sheet 2 has no colour, so Sheet 2 is B&W.
    #
    # Splitting results in:
    #   - Colour sub-job (first): Sheet 1 (imposed pages 1-2, total 2 pages).
    #   - B&W sub-job (second): Sheet 2 (imposed pages 3-4, total 2 pages).
    assert len(captured_calls) == 2

    # Check Colour sub-job
    col_call = captured_calls[0]
    assert col_call["job_id"] == "OSP-E2E-TEST"
    assert col_call["printer_key"] == "epson"
    assert col_call["colour_mode"] == "colour"
    assert col_call["copies"] == 3
    assert col_call["sides"] == "ds"
    assert col_call["paper_size"] == "A4"
    assert col_call["orientation"] == "landscape"  # 2-up landscape
    
    doc_col = fitz.open(col_call["filepath"])
    assert len(doc_col) == 2  # exactly 2 pages
    doc_col.close()

    # Check B&W sub-job
    bw_call = captured_calls[1]
    assert bw_call["job_id"] == "OSP-E2E-TEST"
    assert bw_call["printer_key"] == "konica"
    assert bw_call["colour_mode"] == "bw"
    assert bw_call["copies"] == 3
    assert bw_call["sides"] == "ds"
    assert bw_call["paper_size"] == "A4"
    assert bw_call["orientation"] == "landscape"  # 2-up landscape
    
    doc_bw = fitz.open(bw_call["filepath"])
    assert len(doc_bw) == 2  # exactly 2 pages
    doc_bw.close()

    # Verify temp workspace directory exists (since we mocked cleanup)
    temp_workspace_dir = os.path.join(str(tmp_path / "Assigned"), "temp_OSP-E2E-TEST")
    assert os.path.exists(temp_workspace_dir)

    # Clean up manually
    import shutil
    shutil.rmtree(temp_workspace_dir)
    assert not os.path.exists(temp_workspace_dir)

    conn.close()
