"""Store-PC polling worker for academic project generation.

Replaces the _trigger_generation thread from academic_api.py.
Polls Supabase every ACAD_POLL_INTERVAL seconds for orders in
'chapters_generating' or 'final_generating' status, runs the
osp-academics pipeline locally, uploads the output DOCX to
Supabase Storage, then advances the order to 'chapters_qc' or
'final_qc' and sends a WhatsApp notification.

Run via START_PRINTOSKY.bat on the store PC.
"""

import datetime
import logging
import os
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("academic_pipeline_worker")

# Ensure repo root is on path so sibling modules import cleanly.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

POLL_INTERVAL  = int(os.environ.get("ACAD_POLL_INTERVAL", "30"))  # seconds
STORAGE_BUCKET = "academic-outputs"

_sb = None


def _client():
    global _sb
    if _sb is None:
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(_ROOT, ".env"))
        except ImportError:
            pass
        from supabase import create_client
        url = os.environ["SUPABASE_URL"]
        key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
        _sb = create_client(url, key)
    return _sb


def _set_status(project_id: str, status: str) -> None:
    _client().table("academic_orders").update({
        "status":     status,
        "updated_at": datetime.datetime.utcnow().isoformat(),
    }).eq("project_id", project_id).execute()


def _revert_on_failure(project_id: str, phase: int) -> None:
    """Push order back to a human-reviewable status so staff can retry."""
    revert_to = "advance_paid" if phase == 1 else "chapters_approved"
    try:
        _set_status(project_id, revert_to)
    except Exception as e:
        logger.error(f"{project_id}: revert failed: {e}")


def _process(order: dict) -> None:
    project_id = order["project_id"]
    phase = 1 if order["status"] == "chapters_generating" else 2
    logger.info(f"{project_id}: starting phase {phase} generation")

    try:
        from academic_pipeline import (
            build_phase1_brief, build_phase2_brief,
            write_brief, run_pipeline, get_output_path,
        )
        from academic_whatsapp import notify_chapters_ready, notify_phase2_link

        brief = build_phase1_brief(order) if phase == 1 else build_phase2_brief(order)
        write_brief(project_id, brief)
        result = run_pipeline(project_id)

        if not result.get("success"):
            logger.error(f"{project_id}: pipeline failed: {result.get('error')}")
            _revert_on_failure(project_id, phase)
            return

        output_path = get_output_path(project_id)
        if not output_path or not os.path.exists(output_path):
            logger.error(f"{project_id}: output file missing at {output_path!r}")
            _revert_on_failure(project_id, phase)
            return

        # Upload DOCX to Supabase Storage.
        storage_filename = f"{project_id}-phase{phase}.docx"
        with open(output_path, "rb") as fh:
            content = fh.read()
        _client().storage.from_(STORAGE_BUCKET).upload(
            storage_filename,
            content,
            {"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        )
        public_url: str = _client().storage.from_(STORAGE_BUCKET).get_public_url(storage_filename)

        docx_field  = "phase1_docx_path" if phase == 1 else "phase2_docx_path"
        next_status = "chapters_qc"      if phase == 1 else "final_qc"

        _client().table("academic_orders").update({
            docx_field:   public_url,
            "status":     next_status,
            "updated_at": datetime.datetime.utcnow().isoformat(),
        }).eq("project_id", project_id).execute()

        phone = order["whatsapp_phone"]
        name  = order.get("customer_name", "")
        if phase == 1:
            notify_chapters_ready(phone, name, project_id)
        else:
            notify_phase2_link(phone, name, project_id)

        logger.info(f"{project_id}: phase {phase} complete → {next_status}")

    except Exception as e:
        logger.error(f"{project_id}: generation error: {e}", exc_info=True)
        _revert_on_failure(project_id, phase)


def poll_once() -> None:
    try:
        result = (
            _client()
            .table("academic_orders")
            .select("*")
            .in_("status", ["chapters_generating", "final_generating"])
            .execute()
        )
        orders = result.data or []
        if orders:
            logger.info(f"Found {len(orders)} order(s) to process")
        for order in orders:
            _process(order)
    except Exception as e:
        logger.error(f"poll error: {e}")


def main() -> None:
    logger.info(f"Academic pipeline worker started — polling every {POLL_INTERVAL}s")
    while True:
        poll_once()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
