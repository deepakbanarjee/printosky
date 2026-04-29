"""Supabase CRUD layer for academic project orders.

Cloud replacement for academic_db.py — mirrors its public API so
academic_pipeline_worker.py and api/index.py can swap backends.
Requires SUPABASE_URL + SUPABASE_SERVICE_KEY (or SUPABASE_KEY) env vars.
"""

import datetime
import logging
import os
from typing import Any

logger = logging.getLogger("db_cloud_academic")

_STATUS_FLOW = [
    "order_received",
    "advance_paid",
    "chapters_generating",
    "chapters_qc",
    "chapters_approved",
    "details_collected",
    "final_generating",
    "final_qc",
    "balance_due",
    "balance_paid",
    "delivered",
]

_ALLOWED_COLUMNS: frozenset[str] = frozenset({
    "customer_name", "whatsapp_phone", "course", "topic", "study_area",
    "sample_size", "tables_json", "status", "advance_paid", "balance_paid",
    "advance_amount", "balance_amount", "razorpay_advance_link",
    "razorpay_balance_link", "phase1_docx_path", "phase2_docx_path",
    "drive_url", "payment_mode", "college", "department", "semester", "year",
    "guide_name", "guide_designation", "hod_name", "register_number",
    "revision_note",
})

_sb = None


def _client():
    global _sb
    if _sb is None:
        from supabase import create_client
        url = os.environ["SUPABASE_URL"]
        key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
        _sb = create_client(url, key)
    return _sb


def _now() -> str:
    return datetime.datetime.utcnow().isoformat()


def next_project_id() -> str:
    """Return next PROJ-YYYY-NNN ID using MAX to avoid gaps from deletes."""
    year = datetime.datetime.now().year
    prefix = f"PROJ-{year}-"
    try:
        result = (
            _client()
            .table("academic_orders")
            .select("project_id")
            .ilike("project_id", f"{prefix}%")
            .order("project_id", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            last_n = int(result.data[0]["project_id"].split("-")[-1])
            return f"{prefix}{last_n + 1:03d}"
        return f"{prefix}001"
    except Exception as e:
        logger.error(f"next_project_id error: {e}")
        raise


def create_order(order: dict[str, Any]) -> None:
    """Insert a new academic order row. Raises ValueError on unknown columns."""
    unknown = set(order.keys()) - (_ALLOWED_COLUMNS | {"project_id", "created_at", "updated_at"})
    if unknown:
        raise ValueError(f"Unknown columns: {unknown}")
    row = {**order, "updated_at": _now()}
    try:
        _client().table("academic_orders").insert(row).execute()
    except Exception as e:
        logger.error(f"create_order error: {e}")
        raise


def get_order(project_id: str) -> dict | None:
    """Return order dict or None if not found."""
    try:
        result = (
            _client()
            .table("academic_orders")
            .select("*")
            .eq("project_id", project_id)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"get_order error for {project_id}: {e}")
        return None


def list_orders(status: str | None = None) -> list[dict]:
    """Return all orders newest-first, optionally filtered by status."""
    try:
        q = (
            _client()
            .table("academic_orders")
            .select("*")
            .order("created_at", desc=True)
        )
        if status:
            q = q.eq("status", status)
        result = q.execute()
        return result.data or []
    except Exception as e:
        logger.error(f"list_orders error: {e}")
        return []


def update_status(project_id: str, new_status: str) -> None:
    """Update status column only. Raises ValueError for invalid status, LookupError if not found."""
    if new_status not in _STATUS_FLOW:
        raise ValueError(f"Invalid status '{new_status}'. Must be one of: {_STATUS_FLOW}")
    try:
        result = (
            _client()
            .table("academic_orders")
            .update({"status": new_status, "updated_at": _now()})
            .eq("project_id", project_id)
            .execute()
        )
        if not result.data:
            raise LookupError(f"No order with project_id={project_id!r}")
    except (ValueError, LookupError):
        raise
    except Exception as e:
        logger.error(f"update_status error: {e}")
        raise


def update_fields(project_id: str, **fields: Any) -> None:
    """Update arbitrary allowed columns. Raises ValueError for unknown keys, LookupError if not found."""
    unknown = set(fields.keys()) - _ALLOWED_COLUMNS
    if unknown:
        raise ValueError(f"Unknown columns: {unknown}")
    fields["updated_at"] = _now()
    try:
        result = (
            _client()
            .table("academic_orders")
            .update(fields)
            .eq("project_id", project_id)
            .execute()
        )
        if not result.data:
            raise LookupError(f"No order with project_id={project_id!r}")
    except (ValueError, LookupError):
        raise
    except Exception as e:
        logger.error(f"update_fields error: {e}")
        raise
