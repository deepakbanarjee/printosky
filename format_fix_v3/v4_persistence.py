"""Shared Supabase helpers for the v4 async job pipeline.

Used by both:
  - api/index.py (format-job-create + format-job-status endpoints)
  - api/inngest.py (process_v4_job background function)

Each Vercel function is its own process, but they import the same code
from this module. No shared state at runtime - just shared helpers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# pb_jobs row helpers

def fetch_job(job_id: str) -> dict[str, Any] | None:
    """Read the pb_jobs row for the given id. Returns None if not found."""
    try:
        from db_cloud import _client
        res = (_client().table("pb_jobs")
                .select("*")
                .eq("id", job_id).limit(1).execute())
        if res.data:
            return res.data[0]
        return None
    except Exception as exc:
        logger.error("pb_jobs fetch failed for %s: %s", job_id, exc)
        return None


def mark_processing(job_id: str, stage: str | None = None,
                     pages_total: int | None = None,
                     pages_done: int | None = None) -> None:
    """Set status=processing + started_at, plus optional progress fields.

    Deploy 2C-opt: accepts pages_total/pages_done so the whole job-start
    state can be written in ONE round-trip instead of separate
    mark_processing + update_progress calls. Reduces free-tier DB load.
    """
    try:
        from db_cloud import _client
        update: dict[str, Any] = {
            "status": "processing",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        if stage:
            update["progress_stage"] = stage
        if pages_total is not None:
            update["progress_pages_total"] = pages_total
        if pages_done is not None:
            update["progress_pages_done"] = pages_done
        _client().table("pb_jobs").update(update).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning("mark_processing failed for %s: %s", job_id, exc)


def update_progress(job_id: str, *, stage: str | None = None,
                     pages_done: int | None = None,
                     pages_total: int | None = None) -> None:
    """Mid-run progress update. Best-effort, doesn't raise on failure."""
    update: dict[str, Any] = {}
    if stage is not None:
        update["progress_stage"] = stage
    if pages_done is not None:
        update["progress_pages_done"] = pages_done
    if pages_total is not None:
        update["progress_pages_total"] = pages_total
    if not update:
        return
    try:
        from db_cloud import _client
        _client().table("pb_jobs").update(update).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning("update_progress failed for %s: %s", job_id, exc)


def mark_done(job_id: str, *, download_url: str, result_meta: dict) -> None:
    """Set status=done with the result URL + meta."""
    try:
        from db_cloud import _client
        _client().table("pb_jobs").update({
            "status": "done",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "result_file_token": job_id,
            "result_download_url": download_url,
            "result_meta": result_meta,
        }).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning("mark_done failed for %s: %s", job_id, exc)


def mark_error(job_id: str, error_type: str, error_message: str) -> None:
    """Set status=error with diagnostic info. Best-effort."""
    try:
        from db_cloud import _client
        _client().table("pb_jobs").update({
            "status": "error",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error_type": error_type,
            "error_message": error_message[:500],
        }).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning("mark_error failed for %s: %s", job_id, exc)


# ---------------------------------------------------------------------------
# Storage helpers

def download_source(storage_path: str) -> bytes:
    """Download bytes from Supabase storage (uploads-v2/ prefix expected)."""
    from db_cloud import _client, INCOMING_BUCKET
    return _client().storage.from_(INCOMING_BUCKET).download(storage_path)


def upload_result_docx(job_id: str, docx_bytes: bytes) -> str:
    """Upload result DOCX to previews-v4/<job_id>.docx, return public URL."""
    from db_cloud import _client, INCOMING_BUCKET
    mime_docx = ("application/vnd.openxmlformats-officedocument."
                 "wordprocessingml.document")
    storage_dst = f"project-builder/previews-v4/{job_id}.docx"
    _client().storage.from_(INCOMING_BUCKET).upload(
        path=storage_dst,
        file=docx_bytes,
        file_options={"content-type": mime_docx, "upsert": "true"},
    )
    return _client().storage.from_(INCOMING_BUCKET).get_public_url(storage_dst)


def insert_telemetry_rows(rows: list[dict]) -> None:
    """Batch-insert per-call telemetry into pb_api_calls. Best-effort."""
    if not rows:
        return
    try:
        from db_cloud import _client
        _client().table("pb_api_calls").insert(rows).execute()
    except Exception as exc:
        logger.warning("insert_telemetry_rows failed: %s", exc)


# ---------------------------------------------------------------------------
# Temp storage helpers (Deploy 2C - chunked Inngest steps)
#
# Multi-step Inngest functions can't pass large blobs between steps via
# the step output (Inngest persists step results in their DB; outputs
# above ~1MB get slow or rejected). So large intermediate state (PDF
# bytes, DOCX-extracted images, per-chunk Vision JSON) goes to Supabase
# storage under project-builder/temp/<job_id>/, and step outputs only
# carry small metadata (paths, page counts).
#
# All temp files MUST be cleaned up at the end of a successful run via
# cleanup_temp(). For failed jobs, a separate Vercel Cron will sweep
# stale temp/ directories nightly (not in this commit).

import json as _json

_TEMP_PREFIX = "project-builder/temp"
_PDF_MIME = "application/pdf"
_JSON_MIME = "application/json"


def stage_temp_pdf(job_id: str, pdf_bytes: bytes) -> str:
    """Upload PDF bytes to temp/<job_id>/source.pdf, return storage path."""
    from db_cloud import _client, INCOMING_BUCKET
    path = f"{_TEMP_PREFIX}/{job_id}/source.pdf"
    _client().storage.from_(INCOMING_BUCKET).upload(
        path=path,
        file=pdf_bytes,
        file_options={"content-type": _PDF_MIME, "upsert": "true"},
    )
    return path


def fetch_temp_pdf(path: str) -> bytes:
    """Download bytes from a previously-staged temp path."""
    from db_cloud import _client, INCOMING_BUCKET
    return _client().storage.from_(INCOMING_BUCKET).download(path)


def stage_docx_images(job_id: str,
                       docx_page_images: list[list[bytes]]) -> str | None:
    """Upload DOCX-extracted images as a single packed manifest JSON.

    Encodes images as base64 so the manifest is JSON-serializable.
    Returns path or None if there are no images.
    """
    if not docx_page_images or not any(docx_page_images):
        return None
    import base64
    manifest = {
        "pages": [
            [base64.b64encode(b).decode("ascii") for b in page]
            for page in docx_page_images
        ],
    }
    from db_cloud import _client, INCOMING_BUCKET
    path = f"{_TEMP_PREFIX}/{job_id}/docx_images.json"
    _client().storage.from_(INCOMING_BUCKET).upload(
        path=path,
        file=_json.dumps(manifest).encode("utf-8"),
        file_options={"content-type": _JSON_MIME, "upsert": "true"},
    )
    return path


def fetch_docx_images(path: str | None) -> list[list[bytes]] | None:
    """Reverse of stage_docx_images. None if path empty."""
    if not path:
        return None
    import base64
    raw = fetch_temp_pdf(path)  # generic download
    manifest = _json.loads(raw)
    return [
        [base64.b64decode(b) for b in page]
        for page in manifest.get("pages", [])
    ]


def stage_chunk_json(job_id: str, chunk_idx: int,
                      chunk_data: dict) -> str:
    """Upload one chunk's Vision JSON (pages + telemetry) to temp/."""
    from db_cloud import _client, INCOMING_BUCKET
    path = f"{_TEMP_PREFIX}/{job_id}/chunks/{chunk_idx:04d}.json"
    _client().storage.from_(INCOMING_BUCKET).upload(
        path=path,
        file=_json.dumps(chunk_data).encode("utf-8"),
        file_options={"content-type": _JSON_MIME, "upsert": "true"},
    )
    return path


def fetch_all_chunk_jsons(job_id: str, chunk_count: int) -> list[dict]:
    """Fetch every chunk JSON for a job, in chunk-index order."""
    out = []
    for c_idx in range(chunk_count):
        path = f"{_TEMP_PREFIX}/{job_id}/chunks/{c_idx:04d}.json"
        try:
            raw = fetch_temp_pdf(path)
            out.append(_json.loads(raw))
        except Exception as exc:
            logger.warning("fetch_all_chunk_jsons: missing chunk %d: %s",
                           c_idx, exc)
            out.append({
                "pages": [],
                "telemetry_rows": [],
                "tokens": {"input": 0, "output": 0,
                            "cache_read": 0, "cache_write": 0},
            })
    return out


def cleanup_temp(job_id: str) -> None:
    """Best-effort cleanup of temp/<job_id>/. Failures logged, not raised."""
    try:
        from db_cloud import _client, INCOMING_BUCKET
        client = _client()
        prefix = f"{_TEMP_PREFIX}/{job_id}"
        listed = client.storage.from_(INCOMING_BUCKET).list(prefix)
        paths = []
        for item in (listed or []):
            name = item.get("name") if isinstance(item, dict) else None
            if name:
                paths.append(f"{prefix}/{name}")
        chunks_listed = client.storage.from_(INCOMING_BUCKET).list(
            f"{prefix}/chunks"
        )
        for item in (chunks_listed or []):
            name = item.get("name") if isinstance(item, dict) else None
            if name:
                paths.append(f"{prefix}/chunks/{name}")
        if paths:
            client.storage.from_(INCOMING_BUCKET).remove(paths)
    except Exception as exc:
        logger.warning("cleanup_temp(%s) failed: %s", job_id, exc)
