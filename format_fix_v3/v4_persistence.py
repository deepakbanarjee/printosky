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


def mark_processing(job_id: str, stage: str | None = None) -> None:
    """Set status=processing + started_at (idempotent)."""
    try:
        from db_cloud import _client
        update: dict[str, Any] = {
            "status": "processing",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        if stage:
            update["progress_stage"] = stage
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
