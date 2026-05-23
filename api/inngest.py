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


# Chunked pipeline tuning (Deploy 2C):
# - CHUNK_SIZE small enough that 10 pages x ~5 workers x ~10s/page fits well
#   under Vercel's 300s per-invocation budget with headroom for retries.
# - Each chunk runs in its own Vercel invocation orchestrated by Inngest.
V4_CHUNK_SIZE = 10


def _prep_step(job_id: str) -> dict:
    """First step: read job, download source, convert+stage, return manifest.

    Runs in one Vercel invocation. Output is JSON-serialisable metadata
    (no PDF bytes in the return value -- those are staged to Supabase).
    """
    from format_fix_v3 import v4_persistence as P
    from format_fix_v3 import orchestrator_v3

    job = P.fetch_job(job_id)
    if job is None:
        return {"ok": False, "reason": "job_not_found", "job_id": job_id}

    if job.get("status") == "done":
        return {"ok": True, "reason": "already_done", "job_id": job_id,
                "skip": True}

    storage_path      = job.get("storage_path", "")
    university        = job.get("university") or "ktu"
    original_filename = job.get("original_filename") or ""
    render_kwargs     = job.get("render_kwargs") or {}

    # DB-load optimization (free tier): no intermediate progress writes
    # during prep. Just download + convert + stage to Supabase storage.
    src_bytes = P.download_source(storage_path)
    prep = orchestrator_v3.prepare_chunked(src_bytes)

    pdf_temp_path = P.stage_temp_pdf(job_id, prep["pdf_bytes"])

    docx_images_temp_path = None
    if prep.get("docx_page_images"):
        docx_images_temp_path = P.stage_docx_images(
            job_id, prep["docx_page_images"],
        )

    page_count = prep["page_count"]
    n_chunks = (page_count + V4_CHUNK_SIZE - 1) // V4_CHUNK_SIZE

    # SINGLE write for the whole job-start state (status + started_at +
    # stage + page totals) instead of 3 separate UPDATEs.
    P.mark_processing(
        job_id, stage="vision_pipeline",
        pages_total=page_count, pages_done=0,
    )

    return {
        "ok": True,
        "job_id": job_id,
        "page_count": page_count,
        "n_chunks": n_chunks,
        "chunk_size": V4_CHUNK_SIZE,
        "pdf_temp_path": pdf_temp_path,
        "docx_images_temp_path": docx_images_temp_path,
        "is_docx": prep.get("is_docx", False),
        "university": university,
        "original_filename": original_filename,
        "render_kwargs": render_kwargs,
    }


def _chunk_step(job_id: str, chunk_idx: int, prep: dict) -> dict:
    """One Inngest step = one chunk = pages [start, end). Stages result JSON."""
    from format_fix_v3 import v4_persistence as P
    from format_fix_v3 import orchestrator_v3

    page_count = prep["page_count"]
    chunk_size = prep["chunk_size"]
    start = chunk_idx * chunk_size
    end = min(start + chunk_size, page_count)

    pdf_bytes = P.fetch_temp_pdf(prep["pdf_temp_path"])

    chunk_result = orchestrator_v3.process_chunk(
        pdf_bytes,
        start_idx=start,
        end_idx=end,
        total_pages=page_count,
        max_workers=5,
        job_token=job_id,
        university=prep.get("university") or "ktu",
    )

    # DB-load optimization: chunk step does ZERO DB writes. Result +
    # telemetry rows are staged to Supabase STORAGE (object store, not
    # Postgres). The assemble step batch-inserts all telemetry in one
    # round-trip and computes final progress. Per-chunk progress in the
    # DB is dropped (the Inngest dashboard shows per-step progress for
    # debugging; the frontend shows elapsed time).
    P.stage_chunk_json(job_id, chunk_idx, chunk_result)

    return {
        "ok": True,
        "chunk_idx": chunk_idx,
        "pages_processed": end - start,
        "pages_done_total": end,
        "tokens": chunk_result.get("tokens"),
    }


def _assemble_step(job_id: str, prep: dict) -> dict:
    """Final step: read all chunks, render DOCX, upload, mark done."""
    from format_fix_v3 import v4_persistence as P
    from format_fix_v3 import orchestrator_v3, renderer as _v3renderer

    n_chunks = prep["n_chunks"]
    page_count = prep["page_count"]
    original_filename = prep.get("original_filename") or ""
    render_kwargs = prep.get("render_kwargs") or {}

    # DB-load optimization: no intermediate stage writes. Read chunk
    # JSONs from STORAGE, collect pages + telemetry + tokens.
    chunks = P.fetch_all_chunk_jsons(job_id, n_chunks)
    all_pages = []
    all_telemetry = []
    total_in = total_out = total_cr = total_cw = 0
    for chunk in chunks:
        all_pages.extend(chunk.get("pages") or [])
        all_telemetry.extend(chunk.get("telemetry_rows") or [])
        tok = chunk.get("tokens") or {}
        total_in += tok.get("input", 0)
        total_out += tok.get("output", 0)
        total_cr += tok.get("cache_read", 0)
        total_cw += tok.get("cache_write", 0)

    docx_page_images_extracted = P.fetch_docx_images(
        prep.get("docx_images_temp_path"),
    )

    assembled = orchestrator_v3.assemble_chunked(
        all_pages,
        page_images=None,
        render_kwargs=render_kwargs,
        docx_page_images_extracted=docx_page_images_extracted,
    )

    docx_bytes = assembled.get("docx_bytes") or b""
    if not docx_bytes:
        raise RuntimeError("assemble produced empty DOCX")

    # ONE batch insert of all per-page telemetry (was 1 insert per chunk)
    P.insert_telemetry_rows(all_telemetry)

    download_url = P.upload_result_docx(job_id, docx_bytes)

    try:
        download_filename = _v3renderer.smart_download_filename(
            original_filename, assembled.get("project_title"),
        )
    except Exception:
        download_filename = f"Printosky_{job_id[:8]}.docx"

    cost_usd = (
        (total_in * orchestrator_v3.SONNET_INPUT_USD_PER_1M
         + total_out * orchestrator_v3.SONNET_OUTPUT_USD_PER_1M
         + total_cr * orchestrator_v3.SONNET_CACHE_READ_USD_PER_1M
         + total_cw * orchestrator_v3.SONNET_CACHE_WRITE_USD_PER_1M)
        / 1_000_000
    )

    result_meta = {
        "pages":              assembled.get("page_count"),
        "page_kinds":         assembled.get("page_kinds"),
        "input_tokens":       total_in,
        "output_tokens":      total_out,
        "cache_read_tokens":  total_cr,
        "cache_write_tokens": total_cw,
        "estimated_cost_usd": round(cost_usd, 6),
        "estimated_cost_inr": round(cost_usd * orchestrator_v3.USD_INR, 4),
        "project_title":      assembled.get("project_title"),
        "download_filename":  download_filename,
        "engine_variant":     "chunked",
        "n_chunks":           n_chunks,
    }

    P.mark_done(job_id, download_url=download_url, result_meta=result_meta)
    P.cleanup_temp(job_id)

    return {
        "ok": True,
        "job_id": job_id,
        "download_url": download_url,
        "pages": page_count,
        "rs_cost": result_meta["estimated_cost_inr"],
    }


def _route_step(job_id: str) -> dict:
    """Read the job's route once (cached as an Inngest step output).

    Returns:
        {ok: False, reason}             -- job not found
        {ok: True, skip: True, reason}  -- already done (idempotent re-fire)
        {ok: True, route: str}          -- proceed; route is v2_structured|v4_vision
    """
    from format_fix_v3 import v4_persistence as P
    job = P.fetch_job(job_id)
    if job is None:
        return {"ok": False, "reason": "job_not_found", "job_id": job_id}
    if job.get("status") == "done":
        return {"ok": True, "skip": True, "reason": "already_done",
                "job_id": job_id}
    return {"ok": True, "route": job.get("route") or "v4_vision"}


def _v2_structured_step(job_id: str) -> dict:
    """Single-step path for DOCX-with-heading-styles (route v2_structured).

    Runs the v2 engine (format_fix.run_from_docx) which reads the
    student's existing Word heading styles directly -> accurate chapter
    hierarchy, zero API cost. No chunking needed: v2 is not API-bound, so
    it comfortably fits in one Vercel invocation.
    """
    import tempfile
    from pathlib import Path
    from format_fix_v3 import v4_persistence as P
    from format_fix_v3 import renderer as _v3renderer

    job = P.fetch_job(job_id)
    if job is None:
        return {"ok": False, "reason": "job_not_found", "job_id": job_id}

    storage_path      = job.get("storage_path", "")
    university        = job.get("university") or "ktu"
    original_filename = job.get("original_filename") or ""

    P.mark_processing(job_id, stage="v2_structured")
    src_bytes = P.download_source(storage_path)

    from format_fix.orchestrator import run_from_docx
    tmp_dir = Path(tempfile.gettempdir()) / f"v2_{job_id}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_path = tmp_dir / "output.docx"
    try:
        summary = run_from_docx(
            docx_bytes=src_bytes,
            university_id=university,
            output_path=out_path,
        )
        docx_bytes = out_path.read_bytes() if out_path.exists() else b""
    finally:
        try:
            out_path.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except Exception:
            pass

    if not docx_bytes:
        raise RuntimeError("v2 engine produced empty DOCX")

    download_url = P.upload_result_docx(job_id, docx_bytes)
    try:
        download_filename = _v3renderer.smart_download_filename(
            original_filename, None,
        )
    except Exception:
        download_filename = f"Printosky_{job_id[:8]}.docx"

    pages = summary.get("pages") or summary.get("pages_processed")
    result_meta = {
        "pages":              pages,
        "claims":             summary.get("claims"),
        "estimated_cost_usd": 0,
        "estimated_cost_inr": 0,   # v2 reads styles; no Claude API calls
        "download_filename":  download_filename,
        "engine_variant":     "v2_structured",
    }
    P.mark_done(job_id, download_url=download_url, result_meta=result_meta)
    return {
        "ok": True,
        "job_id": job_id,
        "download_url": download_url,
        "pages": pages,
        "rs_cost": 0,
        "engine_variant": "v2_structured",
    }


@inngest_client.create_function(
    fn_id="process-v4-job",
    trigger=inngest.TriggerEvent(event="pb/job.created"),
    retries=1,
)
def process_v4_job(ctx: inngest.ContextSync) -> dict:
    """Process a v4 format job. Triggered by pb/job.created event.

    Deploy 2C: MULTI-STEP execution. Each step is one Vercel invocation
    (300s budget) so aswathy-class documents (~50 pages) work end-to-end.

    Steps (per ctx.step.run id):
      prepare       -> download source, convert+stage PDF, plan chunks
      chunk-0000..N -> Vision pass on pages [N*10, N*10+10)
      assemble      -> concatenate chunk JSONs, render DOCX, upload, done

    The Python orchestration code re-runs on every Inngest invocation,
    but step.run() returns cached results for already-completed steps
    so only the next pending step actually executes per invocation.

    Event data: {job_id: str}
    """
    from format_fix_v3 import v4_persistence as P

    job_id = ctx.event.data["job_id"]
    logger.info("process_v4_job start job_id=%s", job_id)

    try:
        # Route decision (cached step -> read once regardless of how many
        # invocations the chunked path spans).
        route_info = ctx.step.run("route", lambda: _route_step(job_id))
        if not route_info.get("ok"):
            return route_info          # job not found
        if route_info.get("skip"):
            return route_info          # already done
        route = route_info.get("route", "v4_vision")

        # v2_structured: DOCX with heading styles -> one fast step, no Vision.
        if route == "v2_structured":
            result = ctx.step.run(
                "v2-engine", lambda: _v2_structured_step(job_id),
            )
            logger.info("process_v4_job done (v2) job_id=%s pages=%s",
                        job_id, result.get("pages"))
            return result

        # v4_vision: multi-step chunked Claude Vision pipeline.
        prep = ctx.step.run("prepare", lambda: _prep_step(job_id))
        if not prep.get("ok"):
            return prep
        if prep.get("skip"):
            return prep

        n_chunks = prep["n_chunks"]

        for i in range(n_chunks):
            step_id = f"chunk-{i:04d}"
            ctx.step.run(
                step_id,
                lambda i=i: _chunk_step(job_id, i, prep),
            )

        result = ctx.step.run(
            "assemble",
            lambda: _assemble_step(job_id, prep),
        )
        logger.info(
            "process_v4_job done job_id=%s pages=%s rs=%s",
            job_id, result.get("pages"), result.get("rs_cost"),
        )
        return result

    except Exception as exc:
        logger.error("process_v4_job error job_id=%s: %s",
                     job_id, exc, exc_info=True)
        try:
            P.mark_error(job_id, type(exc).__name__, str(exc))
        except Exception:
            pass
        raise


# Flask app exposing /api/inngest for sync + execution
app = flask.Flask(__name__)

inngest.flask.serve(
    app,
    inngest_client,
    [ping, process_v4_job],
)
