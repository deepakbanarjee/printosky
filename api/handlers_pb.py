"""Project-builder (PrintBuddy) HTTP handlers — extracted from api/index.py.

Backs the /project-builder/* endpoints (templates, analyse, format-preview
v1/v2/v3, format-job create/status, upload-sign, create-order, process, orders).
Each is a plain handler taking the BaseHTTPRequestHandler instance `h`; the
router in api/index.py imports the entry points and dispatches to them.

First slice of the api/index.py split. Shared helpers and the bypass rate-limit
state are imported back from api.index (single source of truth during this
incremental extraction; a later slice moves them to api/_common.py).
"""
import json
import logging
import os
from datetime import datetime
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("api.webhook")

from api.index import (  # noqa: E402  (api.index is mid-import; names below are defined above the import site)
    _json_response,
    _auth_admin_pw,
    _normalize_phone,
    _check_bypass_rate_limit,
    _record_bypass_failure,
    _bypass_attempts,
    _BYPASS_MAX_ATTEMPTS,
)


def _generate_pb_order_id() -> str:
    """Generate a unique project builder order ID: PB-YYYYMMDD-xxxxxx."""
    import uuid
    return f"PB-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"


def _send_pb_whatsapp(phone: str, order_id: str, download_url: str) -> None:
    """Send the formatted document download link via WhatsApp.

    NOTE: Works within the 24-hour Meta service window (customer initiated).
    For students who have never messaged the WABA number, this will fail
    silently — the browser download link is the primary delivery mechanism.
    A pre-approved template should be submitted to Meta for all-India cold reach.
    """
    from whatsapp_notify import _send_meta
    msg = (
        f"✅ Your project report is ready!\n\n"
        f"📄 Download link:\n{download_url}\n\n"
        f"🔖 Order ID: *{order_id}*\n"
        f"Save this — you can re-download at printosky.com/pb-retrieve\n\n"
        f"Need corrections? Reply with your Order ID.\n\n"
        f"— Printosky | printosky.com"
    )
    try:
        _send_meta(phone, msg)
    except Exception as e:
        logger.warning(f"_send_pb_whatsapp failed for {order_id}: {e}")


# ── Project Builder handlers ─────────────────────────────────────────────────

def _pb_docx_response(h, docx_bytes: bytes, filename: str) -> None:
    """Send a .docx file as an HTTP response with CORS headers."""
    h.send_response(200)
    h.send_header(
        "Content-Type",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    h.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    h.send_header("Content-Length", str(len(docx_bytes)))
    h.send_header("Access-Control-Allow-Origin", "*")
    h.end_headers()
    h.wfile.write(docx_bytes)


def _handle_pb_templates_get(h) -> None:
    """GET /project-builder/templates — return list of supported universities."""
    import docx_engine
    unis = docx_engine.list_universities()
    _json_response(h, 200, {"universities": unis})


def _handle_pb_template_download(h, university_id: str) -> None:
    """GET /project-builder/templates/{id} — generate and return free .docx template."""
    import docx_engine
    try:
        docx_bytes = docx_engine.generate_free_template(university_id)
        cfg        = docx_engine.load_university_config(university_id)
    except ValueError as e:
        _json_response(h, 400, {"error": str(e)})
        return
    filename = cfg["short_name"].replace(" ", "_") + "_Project_Template.docx"
    _pb_docx_response(h, docx_bytes, filename)


def _handle_pb_analyse(h, body: bytes) -> None:
    """POST /project-builder/analyse — free chapter detection before payment."""
    import base64
    import docx_engine
    try:
        data = json.loads(body)
        content_b64 = data.get("content_b64", "")
        filename = data.get("filename", "document.docx")

        if not content_b64:
            _json_response(h, 400, {"error": "No file content provided"})
            return

        file_bytes = base64.b64decode(content_b64)
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if ext == "pdf":
            _json_response(h, 400, {
                "error": "PDF upload is not supported. Open the PDF in Word, "
                         "save as .docx, then upload that file."
            })
            return
        if ext != "docx":
            _json_response(h, 400, {"error": "Only .docx files are supported."})
            return

        # Smart 3-pass detection: Word styles → heuristics → Claude metadata
        structure  = docx_engine.detect_structure_from_docx(file_bytes)
        text       = docx_engine.extract_text_from_docx(file_bytes)
        word_count = len(text.split())

        if "error" in structure:
            _json_response(h, 200, {
                "title":      "",
                "chapters":   [],
                "word_count": word_count,
                "structured": False,
            })
            return

        result_chapters = []
        for ch in structure.get("chapters", []):
            words = len(ch.get("content", "").split()) + sum(
                len(s.get("content", "").split()) for s in ch.get("sections", [])
            )
            result_chapters.append({
                "number":     ch.get("number", 0),
                "heading":    ch.get("heading", ""),
                "word_count": words,
            })

        _json_response(h, 200, {
            "title":      structure.get("title", ""),
            "chapters":   result_chapters,
            "word_count": word_count,
            "structured": True,
        })

    except Exception as exc:
        logger.error("pb analyse error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


# ── Back-pressure (P0 Day 2.5d) ──────────────────────────────────────────────
# When the operator queue grows beyond capacity, refuse new Premium-tier
# (currently "generate") orders so the queue doesn't compound. Standard-tier
# (format_fix) orders continue — they rarely escalate.
_PB_PREMIUM_PAUSE_THRESHOLD = int(os.environ.get("PB_OPERATOR_CAPACITY", "10"))


def _premium_paused() -> tuple[bool, dict]:
    """Return (paused, depth_dict). Paused == True when queue total_open
    is at or above the threshold. Failure to query the queue is non-blocking
    (paused=False) so a DB hiccup never freezes paid conversions.
    """
    try:
        from db_cloud import get_operator_queue_depth
        depth = get_operator_queue_depth()
        total = int(depth.get("total_open", 0) or 0)
        return (total >= _PB_PREMIUM_PAUSE_THRESHOLD, depth)
    except Exception as exc:
        logger.warning("premium_paused depth check failed: %s", exc)
        return (False, {"_error": str(exc)})


def _handle_pb_availability(h) -> None:
    """GET /project-builder/availability — public, unauthenticated.

    Tells the customer UI which tiers are accepting new orders right now.
    Used by project-builder.html to grey out a tier card when paused.
    """
    paused, depth = _premium_paused()
    _json_response(h, 200, {
        "standard_available": True,
        "premium_available":  not paused,
        "queue_depth":        depth.get("total_open", 0),
        "threshold":          _PB_PREMIUM_PAUSE_THRESHOLD,
        "message": (
            "Premium tier temporarily paused — our team is finishing in-flight "
            "orders. Try again in a couple of hours, or pick Standard."
            if paused else
            "All tiers accepting new orders."
        ),
    })


def _handle_pb_create_order(h, body: bytes) -> None:
    """POST /project-builder/create-order — create Razorpay order for paid tier."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return

    service    = data.get("service", "")
    university = data.get("university", "")
    word_count = int(data.get("word_count", 0))

    if service not in ("format_fix", "generate"):
        _json_response(h, 400, {"error": "service must be 'format_fix' or 'generate'"})
        return
    if not university:
        _json_response(h, 400, {"error": "university is required"})
        return

    # Back-pressure: refuse Premium-tier order creation if operator queue is
    # at/above capacity. Customer hasn't paid yet — surface a clean upsell
    # to Standard tier or "check back in 2h" rather than letting them pay
    # for something we can't deliver in SLA.
    if service == "generate":
        paused, depth = _premium_paused()
        if paused:
            _json_response(h, 503, {
                "error": "premium_paused",
                "message": (
                    "Premium tier is briefly paused while our editorial team "
                    "finishes earlier orders. Your project will be back online "
                    "within ~2 hours, or you can pick the Standard tier "
                    "(Format-Fix) which is still open."
                ),
                "queue_depth":  depth.get("total_open", 0),
                "threshold":    _PB_PREMIUM_PAUSE_THRESHOLD,
                "retry_after_minutes": 120,
                "alternative_tier":    "format_fix",
            })
            return

    # Pricing (P0 20x-margin tiers, agreed 2026-05-12):
    #   format_fix   = Rs.199  (Standard — Sonnet only, no escalation)
    #   generate <50 = Rs.399  (Standard generate — Sonnet w/ Opus escalation)
    #   generate ≥50 = Rs.999  (Premium — Opus likely)
    # See _handle_pb_availability + _premium_paused() for back-pressure.
    if service == "format_fix":
        amount_inr = 199
    else:
        est_pages  = max(1, word_count // 250)
        amount_inr = 399 if est_pages < 50 else 999

    from razorpay_integration import create_project_order
    result = create_project_order(
        amount_paise=amount_inr * 100,
        receipt=f"pb_{service[:3]}_{university[:6]}",
    )

    if "error" in result:
        _json_response(h, 500, {"error": result["error"]})
        return

    _json_response(h, 200, {
        "razorpay_order_id": result["id"],
        "amount":            result["amount"],
        "currency":          result["currency"],
        "key_id":            os.environ.get("RAZORPAY_KEY_ID", ""),
    })


def _handle_pb_format_preview(h, body: bytes) -> None:
    """POST /project-builder/format-preview — free generation, upload to Supabase, return token.

    No payment required. Generates the DOCX, uploads it under a UUID path, and
    returns the token so the client can show a preview before charging.
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return

    service    = data.get("service", "format_fix")
    university = data.get("university", "")

    if service not in ("format_fix", "generate"):
        _json_response(h, 400, {"error": "invalid service"})
        return

    import docx_engine

    try:
        if service == "format_fix":
            content_b64  = data.get("content_b64", "")
            content_type = data.get("content_type", "text")   # "text"|"docx"|"pdf"
            content      = data.get("content", "")

            if content_b64:
                import base64 as _b64
                file_bytes = _b64.b64decode(content_b64)
                # Unified upload path: extract text from any file type, then
                # route through the Sonnet parser. Customer can upload .docx,
                # .pdf, or paste text — they all reach the same engine.
                # Note: this means uploaded DOCX no longer goes through the
                # in-place restyler (which only worked for docs that already
                # had Word heading styles applied — most student docs don't).
                if content_type == "pdf":
                    text = docx_engine.extract_text_from_pdf(file_bytes)
                elif content_type == "docx":
                    text = docx_engine.extract_text_from_docx(file_bytes)
                else:
                    text = file_bytes.decode("utf-8", errors="replace")
                if not text or len(text.strip()) < 200:
                    _json_response(h, 400, {
                        "error": "extraction_too_short",
                        "message": "We couldn't extract enough text from your file. "
                                   "Open it in Word and paste the content directly, "
                                   "or check that the file isn't password-protected.",
                    })
                    return
                if len(text) > 200_000:
                    _json_response(h, 400, {"error": "Document too large (max ~200k characters)"})
                    return
                # Free preview: Sonnet only. Opus escalation only after
                # payment. format_fix_with_structure returns both the
                # DOCX and the validated structure dict in one Sonnet
                # call (was previously two — ~₹2/preview savings).
                docx_bytes, structure = docx_engine.format_fix_with_structure(
                    text, university, allow_escalation=False,
                )
            elif content:
                text = content
                if len(text) > 200_000:
                    _json_response(h, 400, {"error": "Document too large (max ~200k characters)"})
                    return
                docx_bytes, structure = docx_engine.format_fix_with_structure(
                    text, university, allow_escalation=False,
                )
            else:
                _json_response(h, 400, {"error": "content or content_b64 required"})
                return

        else:  # generate
            form_data = data.get("form_data", {})
            if not form_data:
                _json_response(h, 400, {"error": "form_data required for generate service"})
                return
            docx_bytes = docx_engine.generate_from_form(form_data, university)
            structure = {
                "title": form_data.get("title", ""),
                "chapters": [
                    {"number": i + 1, "heading": ch.get("title") or ch.get("heading", f"Chapter {i + 1}")}
                    for i, ch in enumerate(form_data.get("chapters", []))
                ],
            }

    except ValueError as e:
        _json_response(h, 400, {"error": str(e)})
        return
    except docx_engine.StructureDetectionError as e:
        # Free-preview phase: Sonnet couldn't detect clear structure.
        # Surface an upsell prompt instead of silently generating garbage.
        logger.info(
            "format-preview structure detection failed: errors=%s model=%s",
            e.errors, e.model_used,
        )
        _json_response(h, 422, {
            "error": "structure_not_detected",
            "message": (
                "Your input doesn't have clear chapter structure that our "
                "Standard AI could detect. Upgrade to the Premium tier for "
                "our deepest analysis, or refine your input (add chapter "
                "headings like 'Chapter 1: Introduction') and retry — this "
                "preview is free."
            ),
            "details": {
                "validation_errors": e.errors,
                "model_used":        e.model_used,
            },
            "upsell": {
                "tier":   "premium",
                "reason": "Premium analysis uses our Opus model for ambiguous inputs.",
            },
        })
        return
    except Exception as e:
        logger.error(f"project-builder format-preview generation error: {e}")
        _json_response(h, 500, {"error": "Document generation failed. Please try again."})
        return

    # Upload DOCX to Supabase Storage under a UUID — public bucket, unguessable path
    import uuid as _uuid
    token = str(_uuid.uuid4())
    cfg   = docx_engine.load_university_config(university)
    mime_docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    from db_cloud import upload_file
    url = upload_file(f"project-builder/previews/{token}.docx", docx_bytes, mime_docx)
    if not url:
        _json_response(h, 500, {"error": "Upload failed. Please try again."})
        return

    # Telemetry: one row per v1 job. docx_engine doesn't expose per-call
    # usage stats, so cost is recorded as 0 with a note. This still tells
    # us "v1 was called N times" which is enough for engine-usage analytics.
    _insert_pb_api_call_rows([{
        "job_token": token,
        "engine": "format_fix_v1",
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0,
        "cost_inr": 0,
        "error": "v1_usage_not_instrumented",
        "university": university,
    }])

    _json_response(h, 200, {
        "file_token": token,
        "size_bytes": len(docx_bytes),
        "university": cfg.get("short_name", university.upper()),
        "preview":    structure,
    })


def _handle_pb_upload_sign(h, body: bytes) -> None:
    """POST /project-builder/upload-sign — issue a Supabase signed PUT URL.

    Lets the browser upload a DOCX or PDF directly to Supabase Storage,
    bypassing Vercel's 4.5MB function-payload cap entirely.

    Input  : {filename?: str}   (optional, used only for display)
    Output : {signed_url, storage_path, expires_in}

    The returned storage_path is what the customer's next call to
    /project-builder/format-preview-v2 should pass as {storage_path: ...}
    so the engine can read the binary server-side.

    Path layout: project-builder/uploads-v2/<uuid>/<safe_filename>
    - The uuid prefix prevents filename collisions across customers.
    - <safe_filename> is the caller-provided name sanitised to
      alphanumerics + dot + dash + underscore (or "upload.bin" if blank).
    """
    import re as _re
    import uuid as _uuid

    try:
        data = json.loads(body) if body else {}
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return

    raw_filename = str(data.get("filename") or "").strip()
    safe = _re.sub(r"[^A-Za-z0-9._-]", "_", raw_filename)[:120] or "upload.bin"
    storage_path = f"project-builder/uploads-v2/{_uuid.uuid4()}/{safe}"

    try:
        from db_cloud import _client, INCOMING_BUCKET
        result = _client().storage.from_(INCOMING_BUCKET).create_signed_upload_url(
            storage_path
        )
    except Exception as exc:
        logger.error("upload-sign error type=%s msg=%r", type(exc).__name__, str(exc))
        _json_response(h, 500, {
            "error":    "signed-url mint failed",
            "exc_type": type(exc).__name__,
            "exc_msg":  str(exc)[:300],
        })
        return

    _json_response(h, 200, {
        "signed_url":   result.get("signed_url") or result.get("signedUrl"),
        "storage_path": storage_path,
        "expires_in":   7200,
    })


def _handle_pb_format_preview_v2(h, body: bytes) -> None:
    """POST /project-builder/format-preview-v2 — vendored osp-academics engine.

    Uses the format_fix orchestrator (font-aware, page-by-page handler
    dispatch) instead of the docx_engine Sonnet-detect-chapters path. The
    orchestrator needs a PDF on disk; this handler accepts a base64-encoded
    PDF, writes it to a tmp path, runs the engine, uploads the result.

    Input  : {content_b64: <pdf base64>, university: <id>}
    Output : {file_token, size_bytes, pages, claims, engine: "format_fix_v2"}

    Touches no existing module. Storage path lives under
    project-builder/previews-v2/ so it can't collide with v1 outputs.
    """
    import base64 as _b64
    import tempfile
    import uuid as _uuid
    from pathlib import Path

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return

    university   = data.get("university", "ktu")
    content_b64  = data.get("content_b64", "")
    storage_path = data.get("storage_path", "")

    # Two ways to get the file bytes:
    #   1. storage_path -> server-side download from Supabase (uncapped size)
    #   2. content_b64  -> inline base64 in this request (capped by Vercel)
    pdf_bytes: bytes = b""
    if storage_path:
        # Reject any path outside the dedicated uploads-v2/ prefix so this
        # endpoint can't be used to read arbitrary objects from the bucket.
        if not storage_path.startswith("project-builder/uploads-v2/"):
            _json_response(h, 400, {
                "error": "storage_path must be under project-builder/uploads-v2/",
            })
            return
        try:
            from db_cloud import _client, INCOMING_BUCKET
            pdf_bytes = _client().storage.from_(INCOMING_BUCKET).download(storage_path)
        except Exception as exc:
            logger.error(
                "format-preview-v2 storage download error type=%s msg=%r path=%s",
                type(exc).__name__, str(exc), storage_path,
            )
            _json_response(h, 500, {
                "error":    "storage download failed",
                "exc_type": type(exc).__name__,
                "exc_msg":  str(exc)[:300],
            })
            return
    elif content_b64:
        try:
            pdf_bytes = _b64.b64decode(content_b64)
        except Exception as exc:
            _json_response(h, 400, {"error": f"invalid base64: {exc}"})
            return
    else:
        _json_response(h, 400, {
            "error": "content_b64 or storage_path required",
            "hint":  "for files >3MB, POST /project-builder/upload-sign first, "
                     "PUT the file to signed_url, then send {storage_path} here",
        })
        return

    # Dispatch on magic bytes: %PDF = PDF, PK\x03\x04 = DOCX (ZIP).
    is_pdf  = len(pdf_bytes) >= 200 and pdf_bytes[:4] == b"%PDF"
    is_docx = len(pdf_bytes) >= 200 and pdf_bytes[:4] == b"PK\x03\x04"
    if not (is_pdf or is_docx):
        _json_response(h, 400, {
            "error": "input is not a PDF or DOCX",
            "hint":  "v2 accepts %PDF (PDF) or PK\\x03\\x04 (DOCX/ZIP) magic bytes",
        })
        return

    job_id   = str(_uuid.uuid4())
    tmp_dir  = Path(tempfile.gettempdir()) / f"ff_{job_id}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    docx_out = tmp_dir / "output.docx"

    try:
        if is_pdf:
            pdf_in = tmp_dir / "input.pdf"
            pdf_in.write_bytes(pdf_bytes)
            from format_fix.orchestrator import run as ff_run
            result = ff_run(
                pdf_path      = pdf_in,
                university_id = university,
                output_path   = docx_out,
            )
        else:  # DOCX
            from format_fix.orchestrator import run_from_docx
            result = run_from_docx(
                docx_bytes    = pdf_bytes,
                university_id = university,
                output_path   = docx_out,
            )
        if not docx_out.exists():
            _json_response(h, 500, {"error": "engine did not produce output"})
            return
        docx_bytes = docx_out.read_bytes()
    except Exception as exc:
        logger.error("format-preview-v2 engine error: %s", exc, exc_info=True)
        _json_response(h, 500, {"error": f"engine failure: {type(exc).__name__}"})
        return
    finally:
        try:
            for f in tmp_dir.iterdir():
                f.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except Exception:
            pass

    token     = job_id
    mime_docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    storage_path = f"project-builder/previews-v2/{token}.docx"
    try:
        # Direct storage call (not via db_cloud.upload_file) so the full
        # exception repr bubbles up — db_cloud swallows it as "".
        from db_cloud import _client, INCOMING_BUCKET
        _client().storage.from_(INCOMING_BUCKET).upload(
            path=storage_path,
            file=docx_bytes,
            file_options={"content-type": mime_docx, "upsert": "true"},
        )
        url = _client().storage.from_(INCOMING_BUCKET).get_public_url(storage_path)
    except Exception as exc:
        logger.error("format-preview-v2 upload error type=%s msg=%r path=%s",
                     type(exc).__name__, str(exc), storage_path)
        _json_response(h, 500, {
            "error": "upload failed",
            "exc_type": type(exc).__name__,
            "exc_msg":  str(exc)[:500],
            "path":     storage_path,
        })
        return

    if not url:
        _json_response(h, 500, {"error": "upload returned empty url", "path": storage_path})
        return

    # Telemetry: one zero-cost row per v2 job (no API calls, but useful
    # for engine-usage analytics). Best-effort; doesn't block response.
    _insert_pb_api_call_rows([{
        "job_token": token,
        "engine": "format_fix_v2",
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0,
        "cost_inr": 0,
        "university": university,
    }])

    _json_response(h, 200, {
        "file_token": token,
        "size_bytes": len(docx_bytes),
        "engine":     "format_fix_v2",
        "pages":      result.get("pages"),
        "claims":     result.get("claims"),
        "university": university,
    })


def _insert_pb_api_call_rows(rows: list[dict]) -> None:
    """Batch-insert telemetry rows into pb_api_calls.

    Best-effort: failures are logged but don't break the response. The
    customer should never get a 500 because telemetry write failed.
    """
    if not rows:
        return
    try:
        from db_cloud import _client
        _client().table("pb_api_calls").insert(rows).execute()
    except Exception as exc:
        logger.error(
            "pb_api_calls insert failed (best-effort): %s: %s",
            type(exc).__name__, str(exc)[:300],
        )


def _handle_pb_format_preview_v3(h, body: bytes) -> None:
    """POST /project-builder/format-preview-v3 - Claude Vision hybrid engine.

    Input  : {content_b64} or {storage_path}
             plus optional UI controls:
               original_filename (str)
               page_numbers (bool, default true)
               page_number_style (str: "roman_then_arabic" | "decimal" | "off")
               header_text (str)
               footer_text (str)
    Output : DOCX preview link + per-call cost diagnostics + download_filename

    Cost: ~Rs 2-3 per page (Sonnet 4.5). A 30-page project = ~Rs 60-90.
    Latency: ~80-150s for a 30-page doc (parallel, 5 workers).
    """
    import base64 as _b64
    import uuid as _uuid

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return

    university        = data.get("university", "ktu")
    content_b64       = data.get("content_b64", "")
    storage_path      = data.get("storage_path", "")
    original_filename = data.get("original_filename") or ""
    render_kwargs = {
        "page_numbers":         bool(data.get("page_numbers", True)),
        "page_number_style":    data.get("page_number_style", "roman_then_arabic"),
        "page_number_position": data.get("page_number_position", "center"),
        "header_text":          data.get("header_text", "") or "",
        "footer_text":          data.get("footer_text", "") or "",
    }

    src_bytes: bytes = b""
    if storage_path:
        if not storage_path.startswith("project-builder/uploads-v2/"):
            _json_response(h, 400, {
                "error": "storage_path must be under project-builder/uploads-v2/",
            })
            return
        try:
            from db_cloud import _client, INCOMING_BUCKET
            src_bytes = _client().storage.from_(INCOMING_BUCKET).download(storage_path)
        except Exception as exc:
            logger.error(
                "format-preview-v3 storage download error type=%s msg=%r path=%s",
                type(exc).__name__, str(exc), storage_path,
            )
            _json_response(h, 500, {
                "error":    "storage download failed",
                "exc_type": type(exc).__name__,
                "exc_msg":  str(exc)[:300],
            })
            return
    elif content_b64:
        try:
            src_bytes = _b64.b64decode(content_b64)
        except Exception as exc:
            _json_response(h, 400, {"error": f"invalid base64: {exc}"})
            return
    else:
        _json_response(h, 400, {
            "error": "content_b64 or storage_path required",
        })
        return

    is_pdf  = len(src_bytes) >= 200 and src_bytes[:4] == b"%PDF"
    is_docx = len(src_bytes) >= 200 and src_bytes[:4] == b"PK\x03\x04"
    if not (is_pdf or is_docx):
        _json_response(h, 400, {
            "error": "input is not a PDF or DOCX",
        })
        return

    job_id = str(_uuid.uuid4())
    try:
        from format_fix_v3 import orchestrator_v3, renderer as _v3renderer
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
        docx_bytes = result["docx_bytes"]
        if not docx_bytes:
            _json_response(h, 500, {"error": "v3 engine returned empty docx"})
            return
    except Exception as exc:
        logger.error("format-preview-v3 engine error: %s", exc, exc_info=True)
        _insert_pb_api_call_rows([{
            "job_token": job_id,
            "engine": "format_fix_v3",
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            "university": university,
        }])
        _json_response(h, 500, {
            "error":    f"v3 engine failure: {type(exc).__name__}",
            "exc_msg":  str(exc)[:500],
        })
        return

    # Persist per-call telemetry (best-effort, non-blocking on failure)
    _insert_pb_api_call_rows(result.get("per_call_telemetry", []))

    # Smart download filename
    download_filename = _v3renderer.smart_download_filename(
        original_filename,
        result.get("project_title"),
    )

    token        = job_id
    mime_docx    = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    storage_dst  = f"project-builder/previews-v3/{token}.docx"
    try:
        from db_cloud import _client, INCOMING_BUCKET
        _client().storage.from_(INCOMING_BUCKET).upload(
            path=storage_dst,
            file=docx_bytes,
            file_options={"content-type": mime_docx, "upsert": "true"},
        )
        url = _client().storage.from_(INCOMING_BUCKET).get_public_url(storage_dst)
    except Exception as exc:
        logger.error("format-preview-v3 upload error type=%s msg=%r path=%s",
                     type(exc).__name__, str(exc), storage_dst)
        _json_response(h, 500, {
            "error":    "upload failed",
            "exc_type": type(exc).__name__,
            "exc_msg":  str(exc)[:500],
            "path":     storage_dst,
        })
        return

    if not url:
        _json_response(h, 500, {"error": "upload returned empty url", "path": storage_dst})
        return

    _json_response(h, 200, {
        "file_token":          token,
        "size_bytes":          len(docx_bytes),
        "engine":              "format_fix_v3",
        "pages":               result.get("page_count"),
        "page_kinds":          result.get("page_kinds"),
        "input_tokens":        result.get("total_input_tokens"),
        "output_tokens":       result.get("total_output_tokens"),
        "cache_read_tokens":   result.get("total_cache_read_tokens"),
        "cache_write_tokens":  result.get("total_cache_write_tokens"),
        "estimated_cost_usd":  result.get("estimated_cost_usd"),
        "estimated_cost_inr":  result.get("estimated_cost_inr"),
        "elapsed_seconds":     result.get("elapsed_seconds"),
        "project_title":       result.get("project_title"),
        "download_filename":   download_filename,
        "university":          university,
    })


# =============================================================================
# v4 ASYNC JOB QUEUE (project-builder/format-job-*)
# =============================================================================
#
# v4 splits the v3 sync endpoint into two halves:
#   POST /project-builder/format-job-create  -> insert pb_jobs row, return id
#   GET  /project-builder/format-job-status  -> poll-friendly read of row
#
# In Deploy 1, format-job-create runs the engine INLINE (still 300s cap)
# but with the async-shaped UX (browser submits then polls). Deploy 2 will
# swap inline execution for an Inngest event so the engine runs without
# time limit on Inngest's runtime, unblocking aswathy and larger docs.
#
# The v3 endpoint stays alive untouched -- v4 is a parallel track.


def _v4_engine_inline(job_id: str, src_bytes: bytes, is_pdf: bool,
                       university: str, render_kwargs: dict,
                       original_filename: str) -> dict:
    """Run the v3 engine inline and persist the result + telemetry.

    Returns dict with download_url/download_filename/result_meta on
    success. Raises on failure.
    """
    from format_fix_v3 import orchestrator_v3, renderer as _v3renderer

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

    docx_bytes = result["docx_bytes"]
    if not docx_bytes:
        raise RuntimeError("v4 engine returned empty docx")

    _insert_pb_api_call_rows(result.get("per_call_telemetry", []))

    mime_docx = ("application/vnd.openxmlformats-officedocument."
                 "wordprocessingml.document")
    storage_dst = f"project-builder/previews-v4/{job_id}.docx"
    from db_cloud import _client, INCOMING_BUCKET
    _client().storage.from_(INCOMING_BUCKET).upload(
        path=storage_dst,
        file=docx_bytes,
        file_options={"content-type": mime_docx, "upsert": "true"},
    )
    url = _client().storage.from_(INCOMING_BUCKET).get_public_url(storage_dst)

    download_filename = _v3renderer.smart_download_filename(
        original_filename, result.get("project_title"),
    )

    return {
        "engine": "format_fix_v4",
        "download_url": url,
        "download_filename": download_filename,
        "result_meta": {
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
        },
    }


def _v4_mark_processing(job_id: str) -> None:
    try:
        from db_cloud import _client
        from datetime import datetime, timezone
        _client().table("pb_jobs").update({
            "status": "processing",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning("pb_jobs mark processing failed: %s", exc)


def _v4_mark_done(job_id: str, engine_result: dict) -> None:
    try:
        from db_cloud import _client
        from datetime import datetime, timezone
        _client().table("pb_jobs").update({
            "status": "done",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "result_file_token": job_id,
            "result_download_url": engine_result["download_url"],
            "result_meta": engine_result["result_meta"],
        }).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning("pb_jobs mark done failed: %s", exc)


def _v4_mark_error(job_id: str, error_type: str, error_message: str) -> None:
    try:
        from db_cloud import _client
        from datetime import datetime, timezone
        _client().table("pb_jobs").update({
            "status": "error",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error_type": error_type,
            "error_message": error_message[:500],
        }).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning("pb_jobs mark error failed: %s", exc)


def _handle_pb_format_job_create(h, body: bytes) -> None:
    """POST /project-builder/format-job-create - submit a v4 job.

    Deploy 1: runs the engine INLINE (300s cap, same as v3) and returns
    the result immediately. The response shape is async-job-friendly so
    the frontend's submit-then-poll pattern works today and continues
    to work in Deploy 2 when execution moves to Inngest (no 300s limit).
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return

    storage_path      = data.get("storage_path", "")
    university        = data.get("university", "ktu")
    original_filename = data.get("original_filename") or ""
    contact_phone     = data.get("contact_phone") or None
    render_kwargs = {
        "page_numbers":         bool(data.get("page_numbers", True)),
        "page_number_style":    data.get("page_number_style", "roman_then_arabic"),
        "page_number_position": data.get("page_number_position", "center"),
        "header_text":          data.get("header_text", "") or "",
        "footer_text":          data.get("footer_text", "") or "",
    }

    if not storage_path:
        _json_response(h, 400, {"error": "storage_path required"})
        return
    if not storage_path.startswith("project-builder/uploads-v2/"):
        _json_response(h, 400, {
            "error": "storage_path must be under project-builder/uploads-v2/",
        })
        return

    try:
        from db_cloud import _client, INCOMING_BUCKET
        row = {
            "engine": "format_fix_v4",
            "status": "pending",
            "university": university,
            "original_filename": original_filename,
            "storage_path": storage_path,
            "render_kwargs": render_kwargs,
            "contact_phone": contact_phone,
        }
        ins = _client().table("pb_jobs").insert(row).execute()
        if not ins.data:
            _json_response(h, 500, {"error": "failed to create job row"})
            return
        job_id = ins.data[0]["id"]
    except Exception as exc:
        logger.error("format-job-create insert error: %s", exc, exc_info=True)
        _json_response(h, 500, {
            "error":   "job create failed",
            "exc_type": type(exc).__name__,
            "exc_msg":  str(exc)[:300],
        })
        return

    try:
        src_bytes = _client().storage.from_(INCOMING_BUCKET).download(storage_path)
    except Exception as exc:
        _v4_mark_error(job_id, "download_failed", str(exc))
        _json_response(h, 500, {
            "job_id": job_id,
            "status": "error",
            "error_message": f"source download failed: {str(exc)[:200]}",
        })
        return

    is_pdf  = len(src_bytes) >= 200 and src_bytes[:4] == b"%PDF"
    is_docx = len(src_bytes) >= 200 and src_bytes[:4] == b"PK\x03\x04"
    if not (is_pdf or is_docx):
        _v4_mark_error(job_id, "bad_input", "not a PDF or DOCX")
        _json_response(h, 400, {
            "job_id": job_id,
            "status": "error",
            "error_message": "input is not a PDF or DOCX",
        })
        return

    # Engine routing: analyze the document (no API/DB cost) and pick the
    # engine. DOCX-with-heading-styles -> v2_structured (read structure
    # directly, accurate + cheap). Everything else -> v4_vision (Claude
    # Vision chunked pipeline). The chosen route is persisted on the job
    # row; the Inngest function reads it to dispatch.
    route = "v4_vision"
    route_reason = ""
    try:
        from format_fix_v3 import analyzer
        analysis = analyzer.analyze(src_bytes)
        route = analysis.get("route", "v4_vision")
        route_reason = analysis.get("reason", "")
        if route == "reject":
            _v4_mark_error(job_id, "bad_input",
                           analysis.get("reason", "unsupported input"))
            _json_response(h, 400, {
                "job_id": job_id,
                "status": "error",
                "error_message": analysis.get("reason", "unsupported input"),
            })
            return
    except Exception as exc:
        # Analyzer failure must not block a job; default to Vision.
        logger.warning("analyzer failed for job %s: %s", job_id, exc)
        route = "v4_vision"
        route_reason = f"analyzer_error: {type(exc).__name__}"

    try:
        _client().table("pb_jobs").update({"route": route}).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning("failed to store route for job %s: %s", job_id, exc)

    # Deploy 2B: send the job to Inngest for background processing instead
    # of running inline. The browser sees status="processing" right away
    # and starts polling /format-job-status. The Inngest function
    # (process_v4_job in api/inngest.py) picks up the event, reads the
    # route off the job row, and dispatches to v2_structured or v4_vision.
    try:
        import inngest as _inngest
        import os as _os
        _inngest_client = _inngest.Inngest(
            app_id="printosky",
            is_production=_os.getenv("INNGEST_DEV") is None,
        )
        _inngest_client.send_sync(_inngest.Event(
            name="pb/job.created",
            data={"job_id": job_id},
        ))
    except Exception as exc:
        logger.error("inngest send failed (job %s): %s",
                     job_id, exc, exc_info=True)
        _v4_mark_error(job_id, "inngest_send_failed", str(exc)[:300])
        _json_response(h, 500, {
            "job_id": job_id,
            "status": "error",
            "error_message": f"failed to dispatch job: {type(exc).__name__}: "
                             f"{str(exc)[:200]}",
        })
        return

    _json_response(h, 200, {
        "job_id":       job_id,
        "status":       "processing",
        "engine":       "format_fix_v4",
        "route":        route,
        "route_reason": route_reason,
        "poll_url":     f"/project-builder/format-job-status?id={job_id}",
    })


def _handle_pb_format_job_status(h, qs: dict) -> None:
    """GET /project-builder/format-job-status?id=<uuid>"""
    raw = qs.get("id", "")
    job_id = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, str) else "")
    if not job_id:
        _json_response(h, 400, {"error": "id query param required"})
        return
    try:
        from db_cloud import _client
        res = (_client().table("pb_jobs")
                .select("id, status, progress_pages_done, progress_pages_total, "
                        "progress_stage, result_download_url, result_meta, "
                        "error_message, error_type, original_filename, "
                        "created_at, started_at, completed_at")
                .eq("id", job_id).limit(1).execute())
    except Exception as exc:
        logger.error("format-job-status query error: %s", exc, exc_info=True)
        _json_response(h, 500, {
            "error":   "status query failed",
            "exc_msg": str(exc)[:300],
        })
        return

    if not res.data:
        _json_response(h, 404, {"error": "job not found"})
        return

    row = res.data[0]
    download_filename = None
    if row.get("result_meta"):
        meta = row["result_meta"]
        try:
            from format_fix_v3 import renderer as _v3renderer
            download_filename = _v3renderer.smart_download_filename(
                row.get("original_filename"),
                meta.get("project_title") if isinstance(meta, dict) else None,
            )
        except Exception:
            download_filename = None

    _json_response(h, 200, {
        "job_id":               row["id"],
        "status":               row["status"],
        "progress_pages_done":  row.get("progress_pages_done") or 0,
        "progress_pages_total": row.get("progress_pages_total") or 0,
        "progress_stage":       row.get("progress_stage") or "",
        "result_download_url":  row.get("result_download_url"),
        "download_filename":    download_filename,
        "result_meta":          row.get("result_meta"),
        "error_message":        row.get("error_message"),
        "error_type":           row.get("error_type"),
        "created_at":           row.get("created_at"),
        "started_at":           row.get("started_at"),
        "completed_at":         row.get("completed_at"),
    })


def _handle_pb_process(h, body: bytes) -> None:
    """POST /project-builder/process — verify Razorpay payment, generate DOCX."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return

    # Admin bypass — skip Razorpay if correct admin key provided
    admin_key = data.get("admin_key", "")
    if admin_key:
        client_ip = h.headers.get("x-forwarded-for", h.client_address[0]).split(",")[0].strip()
        if not _check_bypass_rate_limit(client_ip):
            _json_response(h, 429, {"error": "Too many attempts. Try again in 15 minutes."})
            return
        admin_pass = os.environ.get("PB_BYPASS_KEY", "")
        if not admin_pass:
            _json_response(h, 500, {"error": "PB_BYPASS_KEY not set on server"})
            return
        if admin_key != admin_pass:
            _record_bypass_failure(client_ip)
            remaining = _BYPASS_MAX_ATTEMPTS - len(_bypass_attempts[client_ip])
            _json_response(h, 403, {"error": f"Wrong bypass password. {remaining} attempt(s) left."})
            return
        # Password correct — bypass payment
        file_token = data.get("file_token", "")
        if not file_token:
            _json_response(h, 400, {"error": "file_token required for admin download"})
            return
        from db_cloud import get_media_url
        path   = f"project-builder/previews/{file_token}.docx"
        dl_url = get_media_url(path)
        _json_response(h, 200, {"download_url": dl_url})
        return

    payment_id = data.get("razorpay_payment_id", "")
    order_id   = data.get("razorpay_order_id", "")
    signature  = data.get("razorpay_signature", "")

    from razorpay_integration import verify_checkout_payment
    if not verify_checkout_payment(order_id, payment_id, signature):
        _json_response(h, 403, {"error": "Payment verification failed"})
        return

    # Token mode: file was pre-generated by /format-preview — return URL + save order
    file_token = data.get("file_token", "")
    if file_token:
        from db_cloud import get_media_url, save_pb_order
        path   = f"project-builder/previews/{file_token}.docx"
        dl_url = get_media_url(path)

        # Collect optional student metadata from request
        wa_phone     = _normalize_phone(data.get("whatsapp_phone", ""))
        student_name = str(data.get("student_name", ""))[:100]
        service_type = data.get("service", "format_fix")
        university   = data.get("university", "")
        try:
            # Token-mode default = format_fix tier baseline (₹199).
            amount_inr = int(data.get("amount_inr", 199))
        except (ValueError, TypeError):
            amount_inr = 199

        pb_oid = _generate_pb_order_id()
        try:
            save_pb_order(
                order_id=pb_oid,
                tier=service_type,
                university=university,
                whatsapp_phone=wa_phone,
                student_name=student_name,
                razorpay_order_id=order_id,
                razorpay_payment_id=payment_id,
                amount_inr=amount_inr,
                storage_path=path,
                download_url=dl_url,
            )
        except Exception as _e:
            logger.warning(f"save_pb_order non-critical failure for {pb_oid}: {_e}")

        if wa_phone:
            _send_pb_whatsapp(wa_phone, pb_oid, dl_url)

        _json_response(h, 200, {"download_url": dl_url, "order_id": pb_oid})
        return

    service    = data.get("service", "")
    university = data.get("university", "")

    if service not in ("format_fix", "generate"):
        _json_response(h, 400, {"error": "invalid service"})
        return

    import docx_engine

    try:
        if service == "format_fix":
            content_b64  = data.get("content_b64", "")
            content_type = data.get("content_type", "text")  # "text"|"docx"|"pdf"
            content      = data.get("content", "")

            if content_b64:
                import base64 as _b64
                file_bytes = _b64.b64decode(content_b64)
                if content_type == "docx":
                    text = docx_engine.extract_text_from_docx(file_bytes)
                elif content_type == "pdf":
                    text = docx_engine.extract_text_from_pdf(file_bytes)
                else:
                    text = file_bytes.decode("utf-8", errors="replace")
            elif content:
                text = content
            else:
                _json_response(h, 400, {"error": "content or content_b64 required"})
                return

            if len(text) > 200_000:
                _json_response(h, 400, {"error": "Document too large (max ~200k characters)"})
                return

            # Post-payment: allow Opus escalation. Both passes fail -> exception.
            docx_bytes = docx_engine.format_fix(text, university, allow_escalation=True)

        else:  # generate
            form_data = data.get("form_data", {})
            if not form_data:
                _json_response(h, 400, {"error": "form_data required for generate service"})
                return
            docx_bytes = docx_engine.generate_from_form(form_data, university)

    except ValueError as e:
        _json_response(h, 400, {"error": str(e)})
        return
    except docx_engine.StructureDetectionError as e:
        # Post-payment: both Sonnet and Opus failed validation. Route the
        # order to pb_operator_queue and return 202. The operator picks
        # up from the admin dashboard (P0 Day 2.5b) and delivers via
        # WhatsApp within 6h.
        wa_phone     = _normalize_phone(data.get("whatsapp_phone", ""))
        student_name = str(data.get("student_name", ""))[:100]
        logger.error(
            "OPERATOR_QUEUE_HANDOFF | phone=%s university=%s errors=%s model=%s "
            "input_size=%d partial_title=%r",
            wa_phone, university, e.errors, e.model_used,
            len(text) if "text" in locals() else 0,
            (e.partial_structure or {}).get("title", ""),
        )

        # Insert into Supabase queue. Both partial structures live on the
        # exception — the parser packs them under sonnet_structure /
        # opus_structure when both passes fail.
        queue_id = None
        try:
            from db_cloud import enqueue_operator_job
            partial = e.partial_structure or {}
            sonnet_partial = partial if e.model_used == "claude-sonnet-4-6" else None
            opus_partial   = partial if e.model_used == "claude-opus-4-5"   else None
            queue_id = enqueue_operator_job(
                pb_order_id=None,  # pb_orders row will be created at delivery
                customer_phone=wa_phone,
                student_name=student_name,
                university=university,
                tier=data.get("service", "standard"),
                input_text=text if "text" in locals() else "",
                sonnet_partial=sonnet_partial,
                opus_partial=opus_partial,
                last_model_used=e.model_used,
                validation_errors=e.errors,
            )
        except Exception as _q_exc:
            logger.error(
                "Failed to enqueue operator job after Structure failure: %s",
                _q_exc,
            )

        _json_response(h, 202, {
            "status":   "human_finishing",
            "queue_id": queue_id,
            "message": (
                "Your project needs a personal touch — our editorial team is "
                "finishing it now. You'll receive the final document on "
                "WhatsApp within 6 hours."
            ),
            "sla_hours": 6,
            "details": {
                "validation_errors": e.errors,
                "model_used":        e.model_used,
            },
        })
        return
    except Exception as e:
        logger.error(f"project-builder process error: {e}")
        _json_response(h, 500, {"error": "Document generation failed. Please try again."})
        return

    # Upload to storage and return URL (consistent with token-mode response)
    from db_cloud import upload_pb_doc, save_pb_order
    pb_oid = _generate_pb_order_id()
    dl_url = upload_pb_doc(pb_oid, docx_bytes)
    if not dl_url:
        _json_response(h, 500, {"error": "Storage upload failed. Please try again."})
        return

    wa_phone     = _normalize_phone(data.get("whatsapp_phone", ""))
    student_name = str(data.get("student_name", ""))[:100]
    try:
        # Fallback to ₹399 (Standard generate baseline) if client omits the
        # field. Aligned with P0 20x-margin tier scheme.
        amount_inr = int(data.get("amount_inr", 399))
    except (ValueError, TypeError):
        amount_inr = 399

    try:
        save_pb_order(
            order_id=pb_oid,
            tier=service,
            university=university,
            whatsapp_phone=wa_phone,
            student_name=student_name,
            razorpay_order_id=order_id,
            razorpay_payment_id=payment_id,
            amount_inr=amount_inr,
            storage_path=f"project-builder/orders/{pb_oid}.docx",
            download_url=dl_url,
        )
    except Exception as _e:
        logger.warning(f"save_pb_order non-critical failure for {pb_oid}: {_e}")

    if wa_phone:
        _send_pb_whatsapp(wa_phone, pb_oid, dl_url)

    _json_response(h, 200, {"download_url": dl_url, "order_id": pb_oid})


# ── Project Builder — order retrieval (Phase 3) ───────────────────────────────

def _handle_pb_order_get(h, order_id: str) -> None:
    """GET /project-builder/orders/{id}?phone=91XXXXXXXXXX — retrieve an order.

    Requires X-Whatsapp-Phone header or ?phone= query param matching the stored
    phone number. Returns the download_url so the student can re-download.
    """
    from db_cloud import get_pb_order
    # Accept phone from header or query param
    phone = h.headers.get("X-Whatsapp-Phone", "")
    if not phone:
        params = parse_qs(urlparse(h.path).query)
        phone  = params.get("phone", [""])[0]

    order = get_pb_order(order_id, _normalize_phone(phone) if phone else None)
    if not order:
        _json_response(h, 404, {"error": "Order not found or phone number mismatch."})
        return
    _json_response(h, 200, {
        "order_id":     order["id"],
        "tier":         order["tier"],
        "university":   order["university"],
        "download_url": order["download_url"],
        "status":       order["status"],
        "created_at":   order["created_at"],
    })


def _handle_pb_orders_admin(h) -> None:
    """GET /project-builder/orders — list all orders (admin auth required)."""
    admin_pw = h.headers.get("X-Admin-Password", "").strip()
    if not admin_pw:
        params   = parse_qs(urlparse(h.path).query)
        admin_pw = params.get("admin_password", [""])[0]
    if not _auth_admin_pw(admin_pw):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    from db_cloud import list_pb_orders
    orders = list_pb_orders(limit=200)
    _json_response(h, 200, {"orders": orders, "count": len(orders)})


def _handle_pb_order_resend(h, order_id: str) -> None:
    """POST /project-builder/orders/{id}/resend — re-send WhatsApp (admin only)."""
    admin_pw = h.headers.get("X-Admin-Password", "").strip()
    if not _auth_admin_pw(admin_pw):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    from db_cloud import get_pb_order
    order = get_pb_order(order_id, whatsapp_phone=None)
    if not order:
        _json_response(h, 404, {"error": "Order not found"})
        return
    phone = order.get("whatsapp_phone", "")
    dl    = order.get("download_url", "")
    if not phone:
        _json_response(h, 400, {"error": "No WhatsApp phone on record for this order"})
        return
    if not dl:
        _json_response(h, 400, {"error": "No download URL on record for this order"})
        return
    _send_pb_whatsapp(phone, order_id, dl)
    _json_response(h, 200, {"ok": True, "sent_to": phone})


