"""Store-PC polling worker for academic project generation.

Replaces the _trigger_generation thread from academic_api.py.
Polls Supabase for orders in 'chapters_generating' or 'final_generating'
status, runs the osp-academics pipeline locally, uploads the output DOCX to
Supabase Storage, then advances the order to 'chapters_qc' or 'final_qc' and
sends a WhatsApp notification.

Pickup is event-driven: a Supabase Realtime subscription on academic_orders
wakes the loop the instant a human approves a chapter/final generation step,
rather than waiting for the next scheduled cycle. ACAD_POLL_INTERVAL is now a
fallback safety net only, in case the realtime connection drops. Set
ACAD_REALTIME=0 to fall back to poll-only.

Run via START_PRINTOSKY.bat on the store PC.
"""

import datetime
import logging
import os
import sys
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("academic_pipeline_worker")

# Ensure repo root is on path so sibling modules import cleanly.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ops_watchdog import report as _report_health

# Fallback poll interval — only matters when realtime is down or disabled,
# since a live subscription wakes the loop immediately on every change. 15
# minutes is plenty for a safety net (it used to be the primary pickup path
# at 90s, costing ~2.9k requests/day against the Supabase egress quota for a
# queue that is a human-approved status change, not a tight deadline).
POLL_INTERVAL  = int(os.environ.get("ACAD_POLL_INTERVAL", "900"))  # seconds
STORAGE_BUCKET = "academic-outputs"

# Realtime is on by default; ACAD_REALTIME=0 disables the subscription and
# falls back to polling only.
REALTIME_ENABLED = os.environ.get("ACAD_REALTIME", "1").lower() not in ("0", "false", "no")

# Set by the realtime callback (a different thread); the main loop waits on
# it instead of a flat sleep so a change wakes it immediately.
_wake_event = threading.Event()

_sb = None


def _load_env() -> None:
    """Load .env if python-dotenv is present. Some store PCs install from the
    CLAUDE.md pip line (no dotenv) rather than requirements.txt, so the import
    is genuinely optional — a missing dotenv just means env vars come from the
    process environment instead. Shared by _client and the realtime thread so
    there is a single optional-import handler in this file, not one per caller.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_ROOT, ".env"))
    except ImportError:
        pass


def _client():
    global _sb
    if _sb is None:
        _load_env()
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
        import re as _re
        if not _re.fullmatch(r"PROJ-\d{4}-\d{3}", project_id):
            logger.error(f"{project_id}: invalid project_id format — aborting upload")
            _revert_on_failure(project_id, phase)
            return
        storage_filename = f"{project_id}-phase{phase}.docx"
        with open(output_path, "rb") as fh:
            content = fh.read()
        _client().storage.from_(STORAGE_BUCKET).upload(
            storage_filename,
            content,
            {"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             # upsert so re-processing a project (retry after a partial failure)
             # overwrites the object instead of erroring on a duplicate name.
             "upsert": "true"},
        )
        public_url: str = _client().storage.from_(STORAGE_BUCKET).get_public_url(storage_filename)

        docx_field  = "phase1_docx_path" if phase == 1 else "phase2_docx_path"
        next_status = "chapters_qc"      if phase == 1 else "final_qc"

        _client().table("academic_orders").update({
            docx_field:   public_url,
            "status":     next_status,
            "updated_at": datetime.datetime.utcnow().isoformat(),
        }).eq("project_id", project_id).execute()

        # Notify best-effort. The order is already committed at this point
        # (DOCX uploaded + status advanced), so a flaky WhatsApp send must NOT
        # bubble up to the outer except and revert a completed generation.
        try:
            phone = order["whatsapp_phone"]
            name  = order.get("customer_name", "")
            if phase == 1:
                notify_chapters_ready(phone, name, project_id)
            else:
                notify_phase2_link(phone, name, project_id)
        except Exception as e:
            logger.error(f"{project_id}: notify failed (order still completed): {e}")

        logger.info(f"{project_id}: phase {phase} complete → {next_status}")

    except Exception as e:
        logger.error(f"{project_id}: generation error: {e}", exc_info=True)
        _revert_on_failure(project_id, phase)


def poll_once() -> int:
    """Run one poll cycle. Returns the number of orders found this pass (not
    necessarily all successfully processed — a poison row is logged and
    skipped, but still counts as "found", since that's what the realtime
    delivery check cares about: did the fallback poll have to go find this,
    or should a subscription have woken us for it already)."""
    try:
        result = (
            _client()
            .table("academic_orders")
            # Deliberately select("*"): the row is handed whole to
            # build_phase1_brief/build_phase2_brief, which live outside this repo,
            # so narrowing the column list here risks dropping a field the brief
            # builder needs. This query matches zero rows in the common case, so
            # its egress cost is request count — addressed via POLL_INTERVAL.
            .select("*")
            .in_("status", ["chapters_generating", "final_generating"])
            .execute()
        )
        orders = result.data or []
        if orders:
            logger.info(f"Found {len(orders)} order(s) to process")
        for order in orders:
            # Guard each order so one malformed/poison row (e.g. missing
            # project_id) cannot abort processing of the rest of the batch.
            try:
                _process(order)
            except Exception as e:
                oid = order.get("project_id", "<unknown>") if isinstance(order, dict) else "<unknown>"
                logger.error(f"{oid}: _process crashed, skipping (batch continues): {e}")
        return len(orders)
    except Exception as e:
        logger.error(f"poll error: {e}")
        return 0


def _realtime_thread(stop: threading.Event) -> None:
    """Run the Supabase Realtime subscription on its own asyncio loop.

    supabase-py 2.15 exposes Realtime through the **async** client only — the
    sync client's ``channel.subscribe()`` raises ``NotImplementedError``, which
    is what silently dropped this worker back to the slow fallback poll. So the
    subscription lives on an asyncio event loop in a dedicated daemon thread;
    the main loop stays a plain blocking poll and this thread's only job is to
    set ``_wake_event`` when an academic_orders row changes.

    Best-effort: any failure reports to ops_watchdog and returns, leaving the
    fallback poll as the safety net. The realtime client's own auto-reconnect
    handles a dropped socket.
    """
    import asyncio

    _load_env()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
    if not (url and key):
        logger.warning("realtime disabled — SUPABASE_URL / key not set")
        _report_health("academic_worker.realtime", False, "SUPABASE_URL / key not set")
        return

    def _on_change(_payload):
        # Runs on the asyncio thread; threading.Event.set is thread-safe.
        _wake_event.set()

    async def _run() -> None:
        from supabase import create_async_client

        # create_async_client passes `key` to the realtime socket as its token,
        # so the connection authorises as service_role and RLS lets changes
        # through — no separate set_auth needed.
        client = await create_async_client(url, key)
        channel = client.channel("academic-orders")
        channel.on_postgres_changes(
            "*", callback=_on_change, table="academic_orders", schema="public",
        )
        await client.realtime.connect()
        await channel.subscribe()
        logger.info("realtime subscription active for academic_orders")
        _report_health("academic_worker.realtime", True, "subscribed")
        # connect() spins up the client's background listen + heartbeat tasks;
        # keep this loop alive so they keep pumping. On return, asyncio.run
        # cancels those background tasks and closes the socket.
        while not stop.is_set():
            await asyncio.sleep(1.0)

    try:
        asyncio.run(_run())
    except Exception as exc:
        logger.warning(
            f"realtime subscription failed ({exc}) — falling back to polling "
            f"every {POLL_INTERVAL}s"
        )
        _report_health("academic_worker.realtime", False, f"{type(exc).__name__}: {exc}")


def start_realtime() -> threading.Event:
    """Start the Realtime subscription in a background daemon thread. Returns a
    stop Event (callers that run forever can ignore it). Non-blocking — the
    subscription is a latency optimisation over the fallback poll, never the
    source of truth, so it must never delay or block startup.
    """
    stop = threading.Event()
    threading.Thread(
        target=_realtime_thread, args=(stop,),
        name="academic-worker-realtime", daemon=True,
    ).start()
    return stop


def _check_realtime_delivery(found: int, woken_by_realtime: bool) -> None:
    """Secondary signal: is the subscription actually delivering, not just
    connected? See store_puller._check_realtime_delivery for the full
    rationale — mirrored here rather than shared, matching the rest of this
    codebase where each poller stays self-contained.

    Deliberately not gated on store hours (docs/FAIL_LOUD.md rejects an hours
    gate). It only ever fires when an order was actually found, so it is
    silent by construction outside business activity.
    """
    if not found:
        return
    if woken_by_realtime:
        _report_health("academic_worker.realtime_delivery", True,
                        f"found {found} order(s) via realtime wake")
        return
    _report_health(
        "academic_worker.realtime_delivery", False,
        f"found {found} order(s) via the {POLL_INTERVAL}s fallback poll with no prior "
        "realtime wake — subscription is connected but not delivering events "
        "(check Realtime is enabled on the `academic_orders` table in Supabase)",
    )


def main() -> None:
    logger.info(f"Academic pipeline worker started — fallback poll every {POLL_INTERVAL}s, "
                f"realtime={REALTIME_ENABLED}")
    if REALTIME_ENABLED:
        start_realtime()

    # None on the first cycle: there is no preceding wait to judge yet.
    woken_by_realtime = None
    while True:
        found = poll_once()
        if REALTIME_ENABLED and woken_by_realtime is not None:
            _check_realtime_delivery(found, woken_by_realtime)
        # Woken immediately by the realtime callback on a matching row change;
        # otherwise falls through to the next scheduled cycle after POLL_INTERVAL.
        woken_by_realtime = _wake_event.wait(POLL_INTERVAL)
        _wake_event.clear()


if __name__ == "__main__":
    main()
