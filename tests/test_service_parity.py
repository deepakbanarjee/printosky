"""
The two service paths agree — and only write columns that exist.

A service can be booked from the store PC (`print_server /new-service`, local
SQLite) or from order-v2 staff mode (`/order/staff-service`, Supabase), the
second added 2026-09-02 so staff can work off-site. Two callers is how a price,
a deposit or a status starts depending on which machine the counter happened to
use — the exact shape of the konica_jobs split B-10 found. These tests are the
comparison that split never had.
"""

import ast
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import rate_card
import service_jobs

HANDLERS = ROOT / "api" / "handlers_order.py"
MANIFEST = ROOT / "config" / "schema_manifest.yaml"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _fn(source: str, name: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


# ── One implementation of every decision ──────────────────────────────────────

def test_print_server_takes_its_service_constants_from_the_shared_module():
    src = _source(ROOT / "print_server.py")
    assert "SERVICE_DEPOSIT_THRESHOLD = service_jobs.SERVICE_DEPOSIT_THRESHOLD" in src
    assert "SERVICE_DEPOSIT_FRACTION  = service_jobs.SERVICE_DEPOSIT_FRACTION" in src
    assert "_SERVICE_META_INTS  = service_jobs.META_INTS" in src


def test_neither_path_defines_its_own_deposit_arithmetic():
    """The number itself must appear once, in service_jobs."""
    for path in (ROOT / "print_server.py", HANDLERS):
        body = "\n".join(line.split("#")[0] for line in _source(path).splitlines())
        assert "500.0" not in body or "service_jobs" in body
        assert "* 0.5" not in body, f"{path.name} computes a deposit of its own"


def test_the_cloud_handlers_make_no_decision_of_their_own():
    """Every branch that decides money or status must call service_jobs."""
    src = _source(HANDLERS)
    for name, calls in (
        ("_create_service_job",
         ["service_jobs.amount_or_none", "service_jobs.service_status",
          "service_jobs.payment_mode", "service_jobs.deposit_for"]),
        ("_handle_order_staff_photocopy",
         ["service_jobs.photocopy_meta", "service_jobs.resolve_amount",
          "service_jobs.payment_mode"]),
        ("_handle_order_service_quote",
         ["service_jobs.meta_from_params", "service_jobs.deposit_for"]),
    ):
        body = ast.get_source_segment(src, _fn(src, name))
        for call in calls:
            assert call in body, f"{name} does not use {call}"


@pytest.mark.parametrize("total,expected", [
    (0, 0.0), (100, 0.0), (500, 0.0),          # at the threshold: on collection
    (501, 250.5), (1000, 500.0), (1249, 624.5),  # over it: half, to the paisa
    (None, 0.0), ("nonsense", 0.0),           # not a number is not a deposit
])
def test_the_deposit_is_the_same_number_wherever_it_is_asked(total, expected):
    import print_server
    assert service_jobs.deposit_for(total) == expected
    assert print_server._service_deposit_for(total) == expected


@pytest.mark.parametrize("quoted,paid,reason,expected", [
    (400, 0,   "",          "Queued"),   # under the threshold: pay on collection
    (600, 0,   "",          "Draft"),    # over it, nothing paid
    (600, 299, "",          "Draft"),    # not enough
    (600, 300, "",          "Queued"),   # exactly the deposit
    (600, 0,   "owner ok",  "Queued"),   # waived, with a reason
    (600, 0,   "   ",       "Draft"),    # blank is not a reason
])
def test_the_status_rule_is_one_rule(quoted, paid, reason, expected):
    assert service_jobs.service_status(quoted, paid, reason) == expected


def test_a_typed_amount_wins_but_is_recorded_as_an_override():
    out = service_jobs.resolve_amount(100.0, 80.0)
    assert out["amount"] == 80.0 and out["quoted"] == 100.0 and out["overridden"]


def test_nothing_typed_and_nothing_quotable_is_not_billable():
    """Filing a Rs.0 job is a sale that silently vanished; both paths refuse."""
    assert service_jobs.resolve_amount(None, None)["billable"] is False
    assert service_jobs.resolve_amount(None, 0.0)["billable"] is False


def test_a_typed_amount_equal_to_the_quote_is_not_an_override():
    assert service_jobs.resolve_amount(100.0, 100.0)["overridden"] is False


def test_a_bad_payment_mode_falls_back_rather_than_being_stored():
    for raw in ("cash", "Cash", "CASH"):
        assert service_jobs.payment_mode(raw) == "Cash"
    assert service_jobs.payment_mode("upi") == "UPI"
    for raw in ("crypto", "", None, "barter"):
        assert service_jobs.payment_mode(raw) == "Cash"


def test_a_non_numeric_quantity_is_refused_on_both_paths():
    """print_server raised on this from the start. The cloud must not be the
    lenient one — a sheet count that is not a number is a UI bug, and quoting it
    at the default bills the wrong number quietly."""
    import print_server
    with pytest.raises(ValueError):
        service_jobs.meta_from_params({"sheets": ["abc"]})
    with pytest.raises(ValueError):
        print_server._service_meta_from_qs({"sheets": ["abc"]})


def test_both_paths_quote_a_service_identically():
    meta = {"sheets": 40, "lam_type": "pouch", "paper_size": "A4"}
    once = rate_card.calculate_service_quote("laminate", meta)
    twice = rate_card.calculate_service_quote("laminate", meta)
    assert once["total"] == twice["total"]
    # ...and both handlers ask rate_card rather than pricing anything themselves.
    cloud = ast.get_source_segment(_source(HANDLERS),
                                   _fn(_source(HANDLERS), "_create_service_job"))
    assert "rate_card.calculate_service_quote" in cloud


# ── Only columns that exist ───────────────────────────────────────────────────

def _manifest_jobs_columns() -> set:
    m = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    tables = m.get("tables", m)
    return set(tables["jobs"]["columns"])


def _written_columns(fn_name: str) -> set:
    """The literal keys of the `row = {...}` dict the handler inserts."""
    src = _source(HANDLERS)
    node = _fn(src, fn_name)
    for stmt in ast.walk(node):
        if (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Dict)
                and any(getattr(t, "id", "") == "row" for t in stmt.targets)):
            return {k.value for k in stmt.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError(f"{fn_name} has no `row = {{...}}` literal")


#: The functions that build a `jobs` row for the cloud. `_create_service_job`
#: took over from `_handle_order_staff_service` in B-9, when the customer
#: booking path became a second caller of the same row — which is exactly why
#: it is shared rather than copied.
ROW_BUILDERS = ["_create_service_job", "_handle_order_staff_photocopy"]


@pytest.mark.parametrize("handler", ROW_BUILDERS)
def test_every_column_written_to_the_cloud_exists(handler):
    """PostgREST rejects the WHOLE insert on one unknown column, so a stray key
    means the endpoint 500s on every call. `override_reason`, `amount_partial`
    and `queued_at` are local-SQLite-only and were caught exactly here."""
    unknown = _written_columns(handler) - _manifest_jobs_columns()
    assert not unknown, f"{handler} writes columns the cloud jobs table has no: {unknown}"


@pytest.mark.parametrize("column", ["override_reason", "amount_partial", "queued_at"])
def test_the_local_only_columns_stay_local(column):
    assert column not in _manifest_jobs_columns()
    for handler in ROW_BUILDERS:
        assert column not in _written_columns(handler)


def test_a_waived_deposit_still_leaves_a_readable_reason():
    """override_reason has no cloud column, so it goes where the operator looks.
    Dropping it would make a waiver unauditable, which is the point of asking."""
    body = ast.get_source_segment(_source(HANDLERS),
                                  _fn(_source(HANDLERS), "_create_service_job"))
    assert "deposit waived: " in body
    assert '"notes":' in body


# ── The isolation properties, on the cloud path too ───────────────────────────

@pytest.mark.parametrize("handler", ["_create_service_job"])
def test_a_cloud_service_job_never_gets_a_file_url(handler):
    """store_puller pulls only rows with a non-empty file_url. No file_url is
    what makes it structurally impossible for a service job to be auto-printed."""
    assert "file_url" not in _written_columns(handler)


def test_a_cloud_service_job_never_gets_printed_by():
    """printed_by is what keeps services out of the MIS printer and staff panels
    (tests/test_service_ui.py). Setting it here would undo that from the cloud."""
    assert "printed_by" not in _written_columns("_create_service_job")


def test_a_photocopy_is_still_not_a_service_job():
    """B-6's deliberate omission, now on both paths: a photocopy is work the
    Konica actually did, so it stays inside the printer counts — which is what
    makes the B-10 copy/scan reconciliation possible at all."""
    written = _written_columns("_handle_order_staff_photocopy")
    assert "service_kind" not in written
    assert "service_meta" not in written
    assert {"source", "service_type", "page_count", "copies"} <= written


def test_a_photocopy_is_filed_completed_and_paid():
    body = ast.get_source_segment(_source(HANDLERS),
                                  _fn(_source(HANDLERS), "_handle_order_staff_photocopy"))
    assert '"status":           "Completed"' in body
    assert '"completed_at":     now' in body


def test_the_photocopy_row_is_recognised_by_the_reconciliation():
    """The shape written here must be the shape copy_reconciliation counts, or
    B-10 reports every cloud photocopy as unbilled."""
    import copy_reconciliation as cr
    body = ast.get_source_segment(_source(HANDLERS),
                                  _fn(_source(HANDLERS), "_handle_order_staff_photocopy"))
    assert '"source":           "Photocopy"' in body
    assert '"service_type":     "Photocopy"' in body
    assert cr.counter_kind({"source": "Photocopy", "service_type": "Photocopy"}) == "Copy"


def test_a_cloud_service_copy_or_scan_is_recognised_too():
    import copy_reconciliation as cr
    for kind, expected in (("copy", "Copy"), ("scan", "Scan")):
        assert cr.counter_kind({"service_kind": kind, "source": "Service"}) == expected


# ── Routing ───────────────────────────────────────────────────────────────────

def test_the_endpoints_are_routed():
    index = _source(ROOT / "api" / "index.py")
    assert 'self.path == "/order/staff-service"' in index
    assert 'self.path == "/order/staff-photocopy"' in index
    assert 'self.path.startswith("/order/service-quote")' in index


def test_order_paths_reach_the_api_in_the_vercel_config():
    cfg = (ROOT / "vercel.json").read_text(encoding="utf-8")
    assert '"/order/(.*)"' in cfg


@pytest.mark.parametrize("handler", ["_handle_order_staff_service",
                                     "_handle_order_staff_photocopy"])
def test_the_write_endpoints_require_staff_auth(handler):
    """These create billable rows. An unauthenticated one is anyone on the
    internet filing jobs into the shop's revenue."""
    body = ast.get_source_segment(_source(HANDLERS), _fn(_source(HANDLERS), handler))
    assert "_acad_auth_staff(h)" in body
    assert "403" in body


def test_the_quote_endpoint_writes_nothing():
    """It runs on every keystroke; it must not touch the database."""
    body = ast.get_source_segment(_source(HANDLERS),
                                  _fn(_source(HANDLERS), "_handle_order_service_quote"))
    for verb in ("insert(", "update(", "upsert(", "delete("):
        assert verb not in body, verb
