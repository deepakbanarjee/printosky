"""
Drop-off bookings, end to end: the booking, the item, the sweep, the console.

`dropoff.py` decides; nothing there touches a database. This file is about the
parts that do — and about the two places a booking's whole meaning lives:

  * `item_received_at` NULL — the job is real, the work is not startable;
  * a phone number — without one the customer cannot be reminded, and a booking
    cancelled without warning is the thing the sweep exists not to do.
"""

import ast
import json
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dropoff

HANDLERS = ROOT / "api" / "handlers_order.py"
INDEX = ROOT / "api" / "index.py"
CONSOLES = ("jobs.html", "admin.html")


def _src(path):
    return Path(path).read_text(encoding="utf-8-sig")


def _fn(source, name):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"{name} not found")


def _console(name):
    return (ROOT / "website" / name).read_text(encoding="utf-8")


# ── Booking: the counter and the site differ in exactly one thing ─────────────

@pytest.fixture
def api(monkeypatch):
    """handlers_order with a stubbed Supabase, returning (call, rows)."""
    import api.handlers_order as ho
    captured, inserted, updated = {}, [], []

    class _T:
        def __init__(self, name): self.name = name
        def insert(self, row): inserted.append(row); return self
        def update(self, row): updated.append(row); return self
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self): return types.SimpleNamespace(data=[])

    class _C:
        def table(self, n): return _T(n)

    db = types.ModuleType("db_cloud"); db._client = lambda: _C()
    monkeypatch.setitem(sys.modules, "db_cloud", db)
    idx = types.ModuleType("api.index"); idx._acad_auth_staff = lambda h: True
    monkeypatch.setitem(sys.modules, "api.index", idx)
    monkeypatch.setattr(ho, "_json_response",
                        lambda h, st, d: captured.update(status=st, data=d))

    class H: headers = {"X-Staff-Pin": "1234"}

    def call(fn, body):
        captured.clear(); inserted.clear(); updated.clear()
        fn(H(), json.dumps(body).encode())
        return captured, inserted, updated

    return call, ho


LAM = {"kind": "laminate", "meta": {"sheets": 6, "lam_type": "pouch"}}


def test_a_counter_booking_has_the_item_in_hand(api):
    call, ho = api
    got, rows, _ = call(ho._handle_order_staff_service, {**LAM, "store_id": "OSP"})
    assert got["status"] == 200
    assert rows[0]["item_received_at"] is not None
    assert got["data"]["item_in_hand"] is True
    assert got["data"]["expires_in_days"] is None


def test_an_online_booking_does_not(api):
    call, ho = api
    got, rows, _ = call(ho._handle_order_book_service,
                        {**LAM, "phone": "9495706405"})
    assert got["status"] == 200
    assert rows[0]["item_received_at"] is None
    assert got["data"]["item_in_hand"] is False
    assert got["data"]["expires_in_days"] == dropoff.DROPOFF_EXPIRY_DAYS


def test_an_online_booking_without_a_phone_is_refused(api):
    """It would be cancelled in three days with no warning — the one outcome
    the sweep promises never to produce."""
    call, ho = api
    got, rows, _ = call(ho._handle_order_book_service, LAM)
    assert got["status"] == 400
    assert "WhatsApp number is required" in got["data"]["error"]
    assert rows == []


def test_a_customer_cannot_report_their_own_payment(api):
    call, ho = api
    got, rows, _ = call(ho._handle_order_book_service, {
        **LAM, "phone": "9495706405",
        "amount_collected": 9999, "amount_partial": 500,
        "amount_quoted": 1, "override_reason": "trust me"})
    assert got["status"] == 200
    assert rows[0]["amount_collected"] is None
    assert rows[0]["amount_quoted"] == got["data"]["amount_quoted"] > 1
    assert "trust me" not in (rows[0].get("notes") or "")


def test_an_online_booking_is_marked_as_one(api):
    call, ho = api
    _, rows, _ = call(ho._handle_order_book_service, {**LAM, "phone": "9495706405"})
    assert rows[0]["source"] == "Web booking"


def test_the_public_endpoint_takes_no_staff_pin():
    """It is the public order path, like /order/create. If this ever starts
    checking auth, the site's booking form silently stops working."""
    body = _fn(_src(HANDLERS), "_handle_order_book_service")
    assert "_acad_auth_staff" not in body


def test_the_counter_endpoint_still_requires_one():
    body = _fn(_src(HANDLERS), "_handle_order_staff_service")
    assert "_acad_auth_staff(h)" in body and "403" in body


def test_both_paths_go_through_one_creator():
    """Two implementations is how a price or a status starts depending on which
    door the booking came in through."""
    src = _src(HANDLERS)
    for name in ("_handle_order_staff_service", "_handle_order_book_service"):
        assert "_create_service_job(h, data, item_in_hand=" in _fn(src, name), name


# ── The item arrives ──────────────────────────────────────────────────────────

def test_receiving_an_item_is_idempotent():
    """Staff double-tap. Silently moving the timestamp would restart an expiry
    clock that should already be over."""
    body = _fn(_src(HANDLERS), "_handle_order_receive_item")
    assert "already = row.get(\"item_received_at\")" in body
    assert '"already": True' in body


def test_a_print_job_cannot_be_marked_received():
    """Its item is its file. A timestamp there implies a workflow that does not
    exist and would hide the job from nothing."""
    body = _fn(_src(HANDLERS), "_handle_order_receive_item")
    assert "is a print job, not a drop-off booking" in body


def test_receiving_an_item_requires_staff():
    body = _fn(_src(HANDLERS), "_handle_order_receive_item")
    assert "_acad_auth_staff(h)" in body


def test_receiving_an_item_records_who_and_when():
    body = _fn(_src(HANDLERS), "_handle_order_receive_item")
    assert "Item received at" in body and "staff_id" in body


def test_a_missing_job_is_a_404_not_a_silent_success():
    body = _fn(_src(HANDLERS), "_handle_order_receive_item")
    assert "404" in body


# ── The sweep ─────────────────────────────────────────────────────────────────

def test_the_sweep_is_routed_and_authed():
    src = _src(INDEX)
    assert '"/cron/dropoff-sweep"' in src
    body = _fn(src, "_handle_cron_dropoff_sweep")
    assert 'os.environ.get("CRON_SECRET", "")' in body
    assert "401" in body


def test_the_sweep_only_reads_bookings_whose_item_has_not_arrived():
    body = _fn(_src(INDEX), "_handle_cron_dropoff_sweep")
    assert '.not_.is_("service_kind", "null")' in body
    assert '.is_("item_received_at", "null")' in body


def test_the_sweep_makes_no_decision_of_its_own():
    """Every rule lives in dropoff.py, where it is testable without a database."""
    body = _fn(_src(INDEX), "_handle_cron_dropoff_sweep")
    assert "dropoff.sweep(rows, now)" in body
    for bucket in ("dropoff.REMIND", "dropoff.EXPIRE", "dropoff.NEEDS_HUMAN"):
        assert bucket in body, bucket
    # No open-coded thresholds: the day counts belong to dropoff.py.
    assert "timedelta(days=" not in body
    assert "DROPOFF_EXPIRY_DAYS = " not in body
    assert "DROPOFF_REMINDER_HOURS = " not in body


def test_a_failed_send_does_not_mark_the_reminder_as_sent():
    """Marking it sent after a failure would let the booking be cancelled with
    no warning ever delivered."""
    body = _fn(_src(INDEX), "_handle_cron_dropoff_sweep")
    send = body[body.index("for row in plan[dropoff.REMIND]"):
                body.index("for row in plan[dropoff.EXPIRE]")]
    assert "continue        # try again next run rather than marking it sent" in send


def test_a_booking_with_no_phone_is_counted_not_ignored():
    body = _fn(_src(INDEX), "_handle_cron_dropoff_sweep")
    assert "unreachable" in body
    assert "logger.warning" in body


def test_cancelling_writes_the_reason_into_notes():
    body = _fn(_src(INDEX), "_handle_cron_dropoff_sweep")
    assert "dropoff.expiry_reason(row)" in body
    assert '"notes": note' in body
    assert "dropoff.CANCELLED" in body


def test_nothing_is_deleted():
    body = _fn(_src(INDEX), "_handle_cron_dropoff_sweep")
    assert ".delete(" not in body


def test_paid_bookings_reach_a_person():
    body = _fn(_src(INDEX), "_handle_cron_dropoff_sweep")
    assert "_alert_ops(" in body
    assert "dropoff.format_needs_human(needs_human)" in body


def test_a_read_failure_is_reported_not_swallowed():
    body = _fn(_src(INDEX), "_handle_cron_dropoff_sweep")
    assert "logger.error" in body and "500" in body


def test_the_sweep_runs_daily_from_github_actions():
    wf = (ROOT / ".github" / "workflows" / "dropoff-sweep-cron.yml").read_text(encoding="utf-8")
    assert "/cron/dropoff-sweep" in wf
    assert "CRON_SECRET" in wf
    cron = re.search(r'- cron: "([^"]+)"', wf).group(1)
    assert cron.split()[2:] == ["*", "*", "*"], "must run every day"


# ── The console ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", CONSOLES)
def test_the_console_knows_a_booking_is_not_work_ready(name):
    src = _console(name)
    assert "function isAwaitingItem(j)" in src
    assert "function isWorkReady(j)" in src


@pytest.mark.parametrize("name", CONSOLES)
def test_ready_is_disabled_while_the_item_is_missing(name):
    """Marking it ready tells the customer their own paper is waiting for them."""
    src = _console(name)
    assert 'isAwaitingItem(j) ? "disabled" : ""' in src


@pytest.mark.parametrize("name", CONSOLES)
def test_the_console_offers_the_item_received_action(name):
    src = _console(name)
    assert "function receiveItem(" in src
    assert "/order/receive-item" in src
    assert "Item received — start work" in src


@pytest.mark.parametrize("name", CONSOLES)
def test_the_console_shows_whether_the_reminder_went(name):
    src = _console(name)
    assert "dropoff_reminded_at" in src
    assert "No reminder sent yet." in src


@pytest.mark.parametrize("name", CONSOLES)
def test_the_console_expiry_days_match_the_python(name):
    src = _console(name)
    assert f"const DROPOFF_EXPIRY_DAYS = {dropoff.DROPOFF_EXPIRY_DAYS};" in src


@pytest.mark.parametrize("name", CONSOLES)
def test_the_console_reloads_rather_than_faking_the_timestamp(name):
    """An already-received item must show ITS time, not the moment of the click."""
    src = _console(name)
    fn = re.search(r"async function receiveItem\(jobId\) \{.*?\n\}", src, re.S).group(0)
    assert "loadAll()" in fn
    assert "d.already" in fn


@pytest.mark.parametrize("name", CONSOLES)
def test_the_two_consoles_agree(name):
    """These blocks are mirrors; drift between them is a known problem here."""
    def block(n):
        s = _console(n)
        i = s.index("// ── Drop-off bookings")
        return s[i:s.index("async function receiveItem")]
    assert block("jobs.html") == block("admin.html")


# ── The order page ────────────────────────────────────────────────────────────

def _order_ui():
    return (ROOT / "website" / "order" / "order-ui.js").read_text(encoding="utf-8")


def test_customers_can_reach_the_booking_panel():
    js = _order_ui()
    assert "function syncServices()" in js
    fn = re.search(r"function syncServices\(\) \{(.*?)\n\}", js, re.S).group(1)
    assert "if (!STAFF) return;" not in fn, "the panel is for customers too"


def test_a_customer_booking_posts_to_the_public_endpoint():
    js = _order_ui()
    assert "!STAFF ? '/order/book-service'" in js


def test_a_customer_is_not_shown_a_payment_box_they_cannot_pay_into():
    js = _order_ui()
    assert "'ov2-svc-paid-card', 'ov2-svc-mode-card'" in js


def test_a_customer_cannot_book_a_photocopy_as_a_dropoff():
    """It needs the machine and the paper at the same moment — there is nothing
    to leave behind."""
    js = _order_ui()
    assert "STAFF_ONLY_KINDS" in js
    kinds = re.search(r"STAFF_ONLY_KINDS = new Set\(\[(.*?)\]\)", js).group(1)
    assert "'copy'" in kinds


def test_the_customer_is_told_what_happens_next():
    page = (ROOT / "website" / "order-v2.html").read_text(encoding="utf-8")
    assert 'id="ov2-svc-dropoff-note"' in page
    assert "cancelled automatically" in page
    assert f"<b>{dropoff.DROPOFF_EXPIRY_DAYS} days</b>" in page


def test_the_success_message_tells_a_customer_to_bring_the_item():
    """The "nothing is charged" half was dropped when N1 landed: a booking over
    the threshold now CAN be charged a deposit online, and a promise that has
    stopped being true is worse than no promise."""
    js = _order_ui()
    assert "Now bring your item to the shop" in js
    assert "We will WhatsApp you a reminder" in js
    assert "nothing is charged until we have it" not in js
