"""Storage abstraction for pdf-editor.

Vercel serverless has an ephemeral filesystem, so production uses
SupabaseStorage (keyed by file_id). Local dev and tests use
LocalFilesystemStorage backed by ./uploads/.

Backend is chosen via the ``PDF_STORAGE_BACKEND`` env var:
  - unset or ``local``    → LocalFilesystemStorage
  - ``supabase``          → SupabaseStorage (requires SUPABASE_URL + SUPABASE_KEY)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class StorageNotFoundError(FileNotFoundError):
    """Raised when a key is not present in the backing store."""


class Storage(Protocol):
    def save_bytes(self, key: str, data: bytes, content_type: str) -> None: ...
    def load_bytes(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...


def _validate_key(key: str) -> None:
    if not key:
        raise ValueError("storage key must be non-empty")
    if key.startswith("/") or key.startswith("\\"):
        raise ValueError(f"storage key must be relative: {key!r}")
    if ".." in key.replace("\\", "/").split("/"):
        raise ValueError(f"storage key must not contain '..': {key!r}")


class LocalFilesystemStorage:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        _validate_key(key)
        return self.root / key

    def save_bytes(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def load_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise StorageNotFoundError(f"missing key: {key}")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        try:
            return self._path(key).exists()
        except ValueError:
            return False

    def delete(self, key: str) -> None:
        try:
            path = self._path(key)
        except ValueError:
            return
        if path.exists():
            path.unlink()


class SupabaseStorage:
    """Supabase Storage-backed implementation for Vercel production.

    Env vars required:
      - SUPABASE_URL
      - SUPABASE_SERVICE_KEY (preferred) or SUPABASE_KEY
    Bucket name defaults to ``pdf-editor`` — override with PDF_STORAGE_BUCKET.
    """

    def __init__(self, bucket: str | None = None):
        self.bucket = bucket or os.environ.get("PDF_STORAGE_BUCKET", "pdf-editor")
        self._client = None

    def _sb(self):
        if self._client is None:
            from supabase import create_client
            url = os.environ["SUPABASE_URL"]
            key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
            self._client = create_client(url, key)
        return self._client

    def save_bytes(self, key: str, data: bytes, content_type: str) -> None:
        _validate_key(key)
        self._sb().storage.from_(self.bucket).upload(
            path=key,
            file=data,
            file_options={"content-type": content_type, "upsert": "true"},
        )

    def load_bytes(self, key: str) -> bytes:
        _validate_key(key)
        try:
            return self._sb().storage.from_(self.bucket).download(key)
        except Exception as e:
            msg = str(e).lower()
            if "not found" in msg or "not_found" in msg or "404" in msg:
                raise StorageNotFoundError(f"missing key: {key}") from e
            raise

    def exists(self, key: str) -> bool:
        _validate_key(key)
        try:
            self._sb().storage.from_(self.bucket).download(key)
            return True
        except Exception:
            return False

    def delete(self, key: str) -> None:
        _validate_key(key)
        try:
            self._sb().storage.from_(self.bucket).remove([key])
        except Exception:
            pass  # idempotent delete


def get_storage() -> Storage:
    backend = os.environ.get("PDF_STORAGE_BACKEND", "local").lower()
    if backend == "local":
        root = os.environ.get("PDF_STORAGE_LOCAL_ROOT", "uploads")
        return LocalFilesystemStorage(root=root)
    if backend == "supabase":
        return SupabaseStorage()
    raise ValueError(f"unknown PDF_STORAGE_BACKEND: {backend}")
