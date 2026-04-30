"""End-to-end HTTP-layer tests for main.app.

Uses a LocalFilesystemStorage rooted at a pytest tmp_path so no real
filesystem or Supabase call is made.
"""
import importlib

import fitz
import pytest
from fastapi.testclient import TestClient


def _make_pdf(num_pages: int = 2) -> bytes:
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=612, height=792)  # US Letter
        page.insert_text((72, 72), f"Hello page {i}", fontsize=24)
    data = doc.write()
    doc.close()
    return data


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PDF_STORAGE_BACKEND", "local")
    monkeypatch.setenv("PDF_STORAGE_LOCAL_ROOT", str(tmp_path))
    import main
    importlib.reload(main)
    return TestClient(main.app)


class TestUpload:
    def test_rejects_non_pdf_content_type(self, client):
        r = client.post(
            "/upload",
            files={"file": ("x.txt", b"not a pdf", "text/plain")},
        )
        assert r.status_code == 400
        assert "PDF" in r.json()["detail"]

    def test_accepts_valid_pdf_and_returns_file_id(self, client):
        r = client.post(
            "/upload",
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        assert r.status_code == 200
        body = r.json()
        assert "file_id" in body
        assert body["filename"] == "in.pdf"
        assert isinstance(body["pages"], list)
        assert len(body["pages"]) == 2

    def test_persists_to_storage(self, client, tmp_path):
        r = client.post(
            "/upload",
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        file_id = r.json()["file_id"]
        assert (tmp_path / f"{file_id}.pdf").exists()


class TestSplit:
    def test_returns_404_for_unknown_file_id(self, client):
        r = client.post("/split", json={"file_id": "no-such-id"})
        assert r.status_code == 404

    def test_splits_uploaded_pdf_and_returns_pdf_bytes(self, client):
        up = client.post(
            "/upload",
            files={"file": ("in.pdf", _make_pdf(num_pages=2), "application/pdf")},
        )
        file_id = up.json()["file_id"]

        r = client.post(
            "/split",
            json={"file_id": file_id, "direction": "vertical", "ratio": 0.5,
                  "deskew": False},
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content.startswith(b"%PDF")
        out = fitz.open(stream=r.content, filetype="pdf")
        assert len(out) == 4  # 2 pages × vertical split → 4
        out.close()


class TestPageImage:
    def test_returns_404_for_unknown_file_id(self, client):
        r = client.get("/pdf/no-such-id/page/0/image")
        assert r.status_code == 404

    def test_returns_png_for_valid_page(self, client):
        up = client.post(
            "/upload",
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        file_id = up.json()["file_id"]

        r = client.get(f"/pdf/{file_id}/page/0/image")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_returns_404_for_out_of_range_page(self, client):
        up = client.post(
            "/upload",
            files={"file": ("in.pdf", _make_pdf(num_pages=1), "application/pdf")},
        )
        file_id = up.json()["file_id"]
        r = client.get(f"/pdf/{file_id}/page/5/image")
        assert r.status_code == 404


class TestSave:
    def test_returns_404_for_unknown_file_id(self, client):
        r = client.post("/save", json={"file_id": "nope", "modifications": []})
        assert r.status_code == 404

    def test_save_with_no_modifications_returns_pdf(self, client):
        up = client.post(
            "/upload",
            files={"file": ("in.pdf", _make_pdf(), "application/pdf")},
        )
        file_id = up.json()["file_id"]

        r = client.post("/save", json={"file_id": file_id, "modifications": []})
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content.startswith(b"%PDF")
