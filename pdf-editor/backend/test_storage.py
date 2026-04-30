"""Tests for the storage abstraction used by the serverless-compatible
pdf-editor backend.

The backend MUST NOT touch the local filesystem directly in production —
all PDF bytes flow through a Storage implementation so we can swap between
a LocalFilesystemStorage (dev/tests) and a SupabaseStorage (Vercel prod).
"""
import pytest

import storage


class TestLocalFilesystemStorage:
    def test_save_then_load_roundtrip(self, tmp_path):
        s = storage.LocalFilesystemStorage(root=tmp_path)
        key = "abc123.pdf"
        data = b"%PDF-1.4 fake bytes"

        s.save_bytes(key, data, content_type="application/pdf")

        assert s.load_bytes(key) == data

    def test_exists_true_after_save(self, tmp_path):
        s = storage.LocalFilesystemStorage(root=tmp_path)
        s.save_bytes("x.pdf", b"hello", content_type="application/pdf")
        assert s.exists("x.pdf") is True

    def test_exists_false_for_missing(self, tmp_path):
        s = storage.LocalFilesystemStorage(root=tmp_path)
        assert s.exists("missing.pdf") is False

    def test_load_missing_raises_not_found(self, tmp_path):
        s = storage.LocalFilesystemStorage(root=tmp_path)
        with pytest.raises(storage.StorageNotFoundError):
            s.load_bytes("does-not-exist.pdf")

    def test_delete_removes_object(self, tmp_path):
        s = storage.LocalFilesystemStorage(root=tmp_path)
        s.save_bytes("gone.pdf", b"bye", content_type="application/pdf")
        s.delete("gone.pdf")
        assert s.exists("gone.pdf") is False

    def test_delete_missing_is_silent(self, tmp_path):
        s = storage.LocalFilesystemStorage(root=tmp_path)
        # Idempotent deletes are safer for cleanup paths.
        s.delete("never-existed.pdf")

    def test_rejects_path_traversal_key(self, tmp_path):
        s = storage.LocalFilesystemStorage(root=tmp_path)
        with pytest.raises(ValueError):
            s.save_bytes("../escape.pdf", b"x", content_type="application/pdf")

    def test_rejects_absolute_key(self, tmp_path):
        s = storage.LocalFilesystemStorage(root=tmp_path)
        with pytest.raises(ValueError):
            s.save_bytes("/etc/passwd", b"x", content_type="application/pdf")

    def test_creates_root_if_missing(self, tmp_path):
        root = tmp_path / "nested" / "uploads"
        s = storage.LocalFilesystemStorage(root=root)
        s.save_bytes("a.pdf", b"hi", content_type="application/pdf")
        assert (root / "a.pdf").exists()


class TestGetStorageFactory:
    def test_defaults_to_local_filesystem(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PDF_STORAGE_BACKEND", raising=False)
        monkeypatch.setenv("PDF_STORAGE_LOCAL_ROOT", str(tmp_path))
        s = storage.get_storage()
        assert isinstance(s, storage.LocalFilesystemStorage)

    def test_explicit_local_backend(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PDF_STORAGE_BACKEND", "local")
        monkeypatch.setenv("PDF_STORAGE_LOCAL_ROOT", str(tmp_path))
        s = storage.get_storage()
        assert isinstance(s, storage.LocalFilesystemStorage)

    def test_unknown_backend_raises(self, monkeypatch):
        monkeypatch.setenv("PDF_STORAGE_BACKEND", "s3-but-not-implemented")
        with pytest.raises(ValueError):
            storage.get_storage()
