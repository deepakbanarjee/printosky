"""Vercel serverless function exposing /api/inngest for Inngest sync + execution.

This file is a SEPARATE Vercel function from api/index.py. The split is
deliberate:
  - api/index.py: raw http.server BaseHTTPRequestHandler for all the
    existing REST endpoints (legacy, well-tested, do not refactor)
  - api/inngest.py: Flask app required by inngest.flask.serve adapter

Functions registered:
  - ping (trigger pb/ping): sanity-check
  - process_v4_job (trigger pb/job.created): real engine for v4 jobs.
    Single-step in Deploy 2B (one Vercel invocation, 300s cap).
    Deploy 2C will chunk into multiple steps if needed.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import flask
import inngest
import inngest.flask

# Make project root importable so `from format_fix_v3 import ...` works
# even when this file is invoked as a Vercel serverless entry point.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


logger = logging.getLogger(__name__)


# Client: reads INNGEST_EVENT_KEY + INNGEST_SIGNING_KEY from env vars
# in production. The is_production flag controls whether the SDK talks
# to Inngest Cloud (prod) or the local Dev Server (dev).
inngest_client = inngest.Inngest(
    app_id="printosky",
    is_production=os.getenv("INNGEST_DEV") is None,
)


@inngest_client.create_function(
    fn_id="ping",
    trigger=inngest.TriggerEvent(event="pb/ping"),
)
def ping(ctx: inngest.ContextSync) -> dict:
    """Sanity-check function. Returns {ok: True}."""
    return {"ok": True, "echo": ctx.event.data}


@inngest_client.create_function(
    fn_id="process-v4-job",
    trigger=inngest.TriggerEvent(event="pb/job.created"),
    retries=1,
)
def process_v4_job(ctx: inngest.ContextSync) -> dict:
    """Process a v4 format job. Triggered by pb/job.created event.

    Single-step in Deploy 2B - the whole engine runs inside one Vercel
    invocation (300s cap). Deploy 2C will split into multiple steps if
    documents larger than the per-invocation budget need to ship.

    Event data: {job_id: str}
    """
    from format_fix_v3 import v4_persistence as P
    from format_fix_v3 import orchestrator_v3, renderer as _v3renderer

    job_id = ctx.event.data["job_id"]
    logger.info("process_v4_job start job_id=%s", job_id)

    job = P.fetch_job(job_id)
    if job is None:
        logger.warning("process_v4_job: job %s not found, skipping", job_id)
        return {"ok": False, "reason": "job_not_found", "job_id": job_id}

    if job.get("status") == "done":
        # Idempotency: Inngest may re-fire after success on retry. Skip.
        logger.info("process_v4_job: job %s already done, skipping", job_id)
        return {"ok": True, "reason": "already_done", "job_id": job_id}

    storage_path      = job.get("storage_path", "")
    university        = job.get("university") or "ktu"
    original_filename = job.get("original_filename") or ""
    render_kwargs     = job.get("render_kwargs") or {}

    P.mark_processing(job_id, stage="downloading_source")

    try:
        src_bytes = P.download_source(storage_path)

        is_pdf  = len(src_bytes) >= 200 and src_bytes[:4] == b"%PDF"
        is_docx = len(src_bytes) >= 200 and src_bytes[:4] == b"PK\x03\x04"
        if not (is_pdf or is_docx):
            raise ValueError(f"source is not PDF or DOCX (got {src_bytes[:4]!r})")

        P.update_progress(job_id, stage="vision_pipeline")
        if is_pdf:
            result = orchestrator_v3.run_v3_from_pdf(
                src_bytes,
                render_kwargs=render_kwargs,
                job_token=job_id,
                university=university,
            )
        else:
            result = orchestrator_v3.run_v3_from_docx(
                src_bytes,
                render_kwargs=render_kwargs,
                job_token=job_id,
                university=university,
            )

        docx_bytes = result.get("docx_bytes") or b""
        if not docx_bytes:
            raise RuntimeError("engine returned empty DOCX")

        P.insert_telemetry_rows(result.get("per_call_telemetry") or [])

        P.update_progress(job_id, stage="uploading_result")
        download_url = P.upload_result_docx(job_id, docx_bytes)

        try:
            download_filename = _v3renderer.smart_download_filename(
                original_filename, result.get("project_title"),
            )
        except Exception:
            download_filename = f"Printosky_{job_id[:8]}.docx"

        result_meta = {
            "pages":              result.get("page_count"),
            "page_kinds":         result.get("page_kinds"),
            "input_tokens":       result.get("total_input_tokens"),
            "output_tokens":      result.get("total_output_tokens"),
            "cache_read_tokens":  result.get("total_cache_read_tokens"),
            "cache_write_tokens": result.get("total_cache_write_tokens"),
            "estimated_cost_usd": result.get("estimated_cost_usd"),
            "estimated_cost_inr": result.get("estimated_cost_inr"),
            "elapsed_seconds":    result.get("elapsed_seconds"),
            "project_title":      result.get("project_title"),
            "download_filename":  download_filename,
        }

        P.mark_done(job_id, download_url=download_url, result_meta=result_meta)
        logger.info("process_v4_job done job_id=%s pages=%s rs=%s",
                    job_id, result.get("page_count"),
                    result.get("estimated_cost_inr"))
        return {
            "ok": True,
            "job_id": job_id,
            "download_url": download_url,
            "pages": result.get("page_count"),
        }

    except Exception as exc:
        logger.error("process_v4_job error job_id=%s: %s",
                     job_id, exc, exc_info=True)
        P.mark_error(job_id, type(exc).__name__, str(exc))
        raise


# Flask app exposing /api/inngest for sync + execution
app = flask.Flask(__name__)

inngest.flask.serve(
    app,
    inngest_client,
    [ping, process_v4_job],
)
