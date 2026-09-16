"""api/v2 — routing, the envelope, authorisation, and the end-to-end order flow.

Everything runs against the in-memory adapters, so this file touches no network
and needs no fixtures torn down. The point of the in-memory implementation is
that a rule proven here is the same rule the Supabase adapter must satisfy.
"""
import json

import pytest

from api.v2 import app
from api.v2.deps import Settings
from api.v2.http import Request
from api.v2.repos import InMemoryUnitOfWork
from api.v2.router import MethodNotAllowed, Router, compile_pattern
from core.errors import NotFoundError
from core.identity import IdentityRecord, Role, hash_secret

SETTINGS = Settings(session_key="k" * 48, agent_token="agent-secret-token",
                    supabase_url="https://example.supabase.co", supabase_service_key="svc")


@pytest.fixture
def uow():
    app.reset_state()
    unit = InMemoryUnitOfWork()
    digest, salt = hash_secret("4821")
    unit.identities.records.append(
        IdentityRecord("anu", "Anu", Role.MANAGER, ("OSP",), digest, salt)
    )
    digest2, salt2 = hash_secret("9988")
    unit.identities.records.append(
        IdentityRecord("bindu", "Bindu", Role.COUNTER, ("PRINTK",), digest2, salt2)
    )
    return unit


def call(uow, method, path, body=None, token=None, query=None, headers=None):
    from urllib.parse import parse_qs

    hdrs = dict(headers or {})
    if token:
        hdrs["authorization"] = f"Bearer {token}"
    request = Request(
        method=method, path=path, query=parse_qs(query or ""), headers=hdrs,
        body=json.dumps(body).encode() if body is not None else b"",
        request_id="test-req",
    )
    return app.handle(request, uow=uow, settings=SETTINGS)


def login(uow, secret="4821"):
    res = call(uow, "POST", "/v2/auth/login", {"secret": secret})
    assert res.status == 200, res.body
    return res.body["data"]["token"]


# ── Router ───────────────────────────────────────────────────────────────────

def test_path_parameters_are_captured():
    r = Router("/v2")
    r.get("/orders/{order_id}", lambda req, ctx: None)
    route, params = r.match("GET", "/v2/orders/OSP-20260916-1A2B")
    assert params == {"order_id": "OSP-20260916-1A2B"}


def test_a_parameter_does_not_swallow_a_slash():
    pattern = compile_pattern("/v2/orders/{order_id}")
    assert pattern.match("/v2/orders/a/b") is None
    assert pattern.match("/v2/orders//pay") is None


def test_wrong_method_on_a_real_path_is_405_with_allow(uow):
    res = call(uow, "DELETE", "/v2/orders/X")
    assert res.status == 405
    assert "GET" in res.headers["Allow"]


def test_an_unknown_v2_path_is_404(uow):
    res = call(uow, "GET", "/v2/nope")
    assert res.status == 404
    assert res.body["error"]["code"] == "not_found"


def test_duplicate_routes_are_a_programming_error():
    r = Router("/v2")
    r.get("/x", lambda req, ctx: None)
    with pytest.raises(ValueError, match="duplicate"):
        r.get("/x", lambda req, ctx: None)


def test_the_router_only_claims_v2():
    """The whole compatibility guarantee: nothing else is touched."""
    router = app.build_router()
    for legacy in ("/order/quote", "/whatsapp-webhook", "/webhook/razorpay", "/admin/send",
                   "/academic/orders", "/cron/sla-check", "/health", "/", "/v20/x", "/v2x"):
        assert router.owns(legacy) is False, legacy
    assert router.owns("/v2/health") is True


# ── Envelope and errors ──────────────────────────────────────────────────────

def test_every_response_uses_the_same_envelope(uow):
    ok = call(uow, "GET", "/v2/health")
    assert set(ok.body) >= {"ok", "data"}
    bad = call(uow, "GET", "/v2/nope")
    assert bad.body["ok"] is False
    assert set(bad.body["error"]) == {"code", "message", "details"}


def test_an_unexpected_exception_becomes_an_opaque_500(uow):
    """A defect must be a logged 500, never a plausible-looking 200."""
    def explode(request, ctx):
        raise RuntimeError("secret internal detail")

    app.build_router().get("/boom", explode, auth="none")
    try:
        res = call(uow, "GET", "/v2/boom")
        assert res.status == 500
        assert res.body["error"]["code"] == "internal_error"
        assert "secret internal detail" not in json.dumps(res.body)
    finally:
        app.reset_state()     # the route table is module-level; do not leak /boom


def test_malformed_json_is_a_400(uow):
    from urllib.parse import parse_qs

    request = Request("POST", "/v2/auth/login", query=parse_qs(""), body=b"{not json")
    res = app.handle(request, uow=uow, settings=SETTINGS)
    assert res.status == 400


# ── Health ───────────────────────────────────────────────────────────────────

def test_health_separates_its_signals(uow):
    res = call(uow, "GET", "/v2/health")
    checks = res.body["data"]["checks"]
    assert checks["alive"]["ok"] is True
    assert checks["database"]["ok"] is None          # not probed unless asked
    deep = call(uow, "GET", "/v2/health", query="deep=1")
    assert deep.body["data"]["checks"]["database"]["ok"] is True


def test_an_unhealthy_deep_probe_is_reported_not_swallowed(uow):
    def broken(**kwargs):
        raise RuntimeError("table missing")

    uow.orders.list_orders = broken
    res = call(uow, "GET", "/v2/health", query="deep=1")
    assert res.status == 503
    assert res.body["ok"] is False
    assert "table missing" in res.body["data"]["checks"]["database"]["detail"]


# ── Auth (F01) ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("secret", ["0000", "wrong-password", "48211", ""])
def test_a_wrong_credential_never_returns_a_token(uow, secret):
    res = call(uow, "POST", "/v2/auth/login", {"secret": secret})
    assert res.status in (400, 401)
    assert "token" not in json.dumps(res.body)


def test_an_unconfigured_deployment_refuses_to_mint_sessions(uow):
    from urllib.parse import parse_qs

    unconfigured = Settings(session_key="")
    request = Request("POST", "/v2/auth/login", query=parse_qs(""),
                      body=json.dumps({"secret": "4821"}).encode())
    res = app.handle(request, uow=uow, settings=unconfigured)
    assert res.status == 503
    assert res.body["error"]["code"] == "not_configured"


def test_repeated_failures_are_throttled(uow):
    for _ in range(5):
        call(uow, "POST", "/v2/auth/login", {"secret": "0000"})
    res = call(uow, "POST", "/v2/auth/login", {"secret": "4821"})
    assert res.status == 401
    assert res.body["error"]["code"] == "too_many_attempts"


def test_a_protected_route_needs_a_token(uow):
    assert call(uow, "GET", "/v2/queue").status == 401


def test_a_revoked_session_stops_working(uow):
    token = login(uow)
    assert call(uow, "GET", "/v2/auth/me", token=token).status == 200
    call(uow, "POST", "/v2/auth/logout", token=token)
    assert call(uow, "GET", "/v2/auth/me", token=token).status == 401


def test_the_agent_token_is_scoped_to_the_store_it_declares(uow):
    res = call(uow, "GET", "/v2/agent/queue", token="agent-secret-token",
               query="store_id=OSP", headers={"x-store-id": "OSP", "x-device-id": "OSP:counter"})
    assert res.status == 200
    cross = call(uow, "GET", "/v2/agent/queue", token="agent-secret-token",
                 query="store_id=PRINTK", headers={"x-store-id": "OSP"})
    assert cross.status == 403


# ── Authorisation (F09) ──────────────────────────────────────────────────────

def test_a_counter_cannot_refund(uow):
    token = login(uow, "9988")          # Bindu, counter at PRINTK
    res = call(uow, "POST", "/v2/orders/X/refunds", {"amount_paise": 100, "reference": "r"},
               token=token)
    assert res.status == 403
    assert res.body["error"]["code"] == "forbidden"


def test_a_manager_cannot_reach_another_store(uow):
    token = login(uow)                  # Anu, manager at OSP
    res = call(uow, "POST", "/v2/orders", {
        "store_id": "PRINTK", "customer_phone": "919000000000",
        "items": [{"pages": 2, "paper_type": "A4_BW"}],
        "sources": [{"object_id": "orders/x/1.pdf"}],
    }, token=token)
    assert res.status == 403


# ── The order lifecycle ──────────────────────────────────────────────────────

def make_order(uow, token, lane="express"):
    res = call(uow, "POST", "/v2/orders", {
        "store_id": "OSP", "customer_phone": "919495706405", "customer_name": "Asha",
        "items": [{"pages": 10, "paper_type": "A4_BW", "copies": 2},
                  {"pages": 3, "paper_type": "A4_col"}],
        "sources": [{"object_id": "orders/a/1.pdf", "file_name": "notes.pdf"},
                    {"object_id": "orders/a/2.pdf", "file_name": "cover.pdf"}],
        "finishing": "spiral", "lane": lane,
    }, token=token)
    assert res.status == 201, res.body
    return res.body["data"]["order"]


def test_create_accept_pay_and_hand_over(uow):
    token = login(uow)
    order = make_order(uow, token)
    assert order["total_paise"] > 0
    assert sum(i["amount_paise"] for i in order["items"]) == order["total_paise"]

    accepted = call(uow, "POST", f"/v2/orders/{order['order_id']}/accept", {}, token=token)
    assert accepted.body["data"]["order"]["production_state"] == "queued"

    paid = call(uow, "POST", f"/v2/orders/{order['order_id']}/payments", {
        "amount_paise": order["total_paise"], "method": "upi", "reference": "UPI-1",
    }, token=token)
    assert paid.body["data"]["order"]["payment_state"] == "paid"
    assert sum(a["amount_paise"] for a in paid.body["data"]["allocations"]) == order["total_paise"]


def test_a_file_url_is_not_accepted_in_place_of_an_object_id(uow):
    """Review finding F08: the public creator used to take any URL."""
    token = login(uow)
    res = call(uow, "POST", "/v2/orders", {
        "store_id": "OSP", "customer_phone": "919495706405",
        "items": [{"pages": 2, "paper_type": "A4_BW"}],
        "sources": [{"file_url": "http://192.168.55.110/admin"}],
    }, token=token)
    assert res.status == 400
    assert "object_id" in res.body["error"]["message"]


def test_a_pricing_failure_does_not_create_a_free_order(uow, monkeypatch):
    import api.v2.routes_orders as routes

    def broken(*args, **kwargs):
        raise RuntimeError("rate card down")

    monkeypatch.setattr(routes, "price_order", broken)
    token = login(uow)
    res = call(uow, "POST", "/v2/orders", {
        "store_id": "OSP", "customer_phone": "919495706405",
        "items": [{"pages": 2, "paper_type": "A4_BW"}],
        "sources": [{"object_id": "orders/a/1.pdf"}],
    }, token=token)
    assert res.status == 500      # an unexpected error is not a ₹0 order either
    assert uow.orders.list_orders(limit=10)[0] == []


def test_handover_is_blocked_while_money_is_outstanding(uow):
    token = login(uow)
    order = make_order(uow, token)
    call(uow, "POST", f"/v2/orders/{order['order_id']}/accept", {}, token=token)
    for state in ("claimed", "spooled", "printed", "ready"):
        call(uow, "POST", f"/v2/orders/{order['order_id']}/transition", {"to": state}, token=token)
    res = call(uow, "POST", f"/v2/orders/{order['order_id']}/transition",
               {"to": "delivered"}, token=token)
    assert res.status == 409
    assert "balance" in res.body["error"]["message"]


def test_an_illegal_transition_is_a_409_not_a_silent_write(uow):
    token = login(uow)
    order = make_order(uow, token)
    res = call(uow, "POST", f"/v2/orders/{order['order_id']}/transition",
               {"to": "delivered"}, token=token)
    assert res.status == 409


def test_an_unknown_state_name_is_a_400_listing_the_real_ones(uow):
    token = login(uow)
    order = make_order(uow, token)
    res = call(uow, "POST", f"/v2/orders/{order['order_id']}/transition",
               {"to": "teleported"}, token=token)
    assert res.status == 400
    assert "queued" in res.body["error"]["details"]["allowed"]


def test_a_counter_payment_needs_a_reference_so_it_cannot_double_count(uow):
    token = login(uow)
    order = make_order(uow, token)
    res = call(uow, "POST", f"/v2/orders/{order['order_id']}/payments",
               {"amount_paise": 100, "method": "cash"}, token=token)
    assert res.status == 400


def test_the_same_counter_payment_twice_is_counted_once(uow):
    token = login(uow)
    order = make_order(uow, token)
    body = {"amount_paise": 5000, "method": "cash", "reference": "RCPT-9"}
    first = call(uow, "POST", f"/v2/orders/{order['order_id']}/payments", body, token=token)
    second = call(uow, "POST", f"/v2/orders/{order['order_id']}/payments", body, token=token)
    assert first.body["data"]["duplicate"] is False
    assert second.body["data"]["duplicate"] is True
    assert second.body["data"]["order"]["paid_paise"] == 5000


def test_a_payment_for_an_unknown_order_is_held_not_dropped(uow):
    token = login(uow)
    res = call(uow, "POST", "/v2/orders/NOPE-1/payments",
               {"amount_paise": 5000, "method": "cash", "reference": "R1"}, token=token)
    assert res.status == 404
    assert "reconciliation" in res.body["error"]["message"]
    # The event is durably in the inbox, flagged, for a human to match up.
    assert any(r["result"] == "unmatched" for r in uow.inbox._rows.values())


def test_the_replay_endpoint_finishes_an_interrupted_commit(uow):
    """'A crash after inbox receipt but before payment commit is recoverable.'"""
    from core.payments import InboxEvent

    token = login(uow)
    order = make_order(uow, token)
    stranded = InboxEvent("razorpay", "evt-crash", "pay-crash", order["order_id"],
                          order["total_paise"], method="upi")
    uow.inbox.accept(stranded)                      # inbox committed, process died

    res = call(uow, "POST", "/v2/payments/replay", {}, token=token)
    assert res.status == 200
    assert res.body["data"]["count"] == 1
    recovered = call(uow, "GET", f"/v2/orders/{order['order_id']}", token=token)
    assert recovered.body["data"]["order"]["payment_state"] == "paid"

    again = call(uow, "POST", "/v2/payments/replay", {}, token=token)
    assert again.body["data"]["count"] == 0        # idempotent: nothing left, nothing doubled


def test_an_order_edited_concurrently_loses_the_race_loudly(uow):
    token = login(uow)
    order = make_order(uow, token)
    stored = uow.orders.get_order(order["order_id"])
    uow.orders.save_order(stored, expected_version=stored.version)   # someone else wrote
    from core.errors import ConflictError

    with pytest.raises(ConflictError):
        uow.orders.save_order(stored, expected_version=stored.version - 1)


# ── The operator queue ───────────────────────────────────────────────────────

def test_the_queue_puts_exceptions_first(uow):
    token = login(uow)
    order = make_order(uow, token, lane="assisted")
    res = call(uow, "GET", "/v2/queue", token=token, query="store_id=OSP")
    buckets = list(res.body["data"]["buckets"])
    assert buckets[0] == "exceptions"
    call(uow, "POST", f"/v2/orders/{order['order_id']}/accept", {}, token=token)
    res = call(uow, "GET", "/v2/queue", token=token, query="store_id=OSP")
    assert res.body["data"]["counts"]["exceptions"] == 1   # awaiting approval


def test_the_agent_queue_only_offers_dispatchable_work(uow):
    token = login(uow)
    order = make_order(uow, token)
    call(uow, "POST", f"/v2/orders/{order['order_id']}/accept", {}, token=token)
    call(uow, "POST", f"/v2/orders/{order['order_id']}/payments",
         {"amount_paise": order["total_paise"], "method": "upi", "reference": "U1"}, token=token)

    res = call(uow, "GET", "/v2/agent/queue", token=token, query="store_id=OSP")
    assert res.body["data"]["count"] == 0        # preflight has not run

    stored = uow.orders.get_order(order["order_id"])
    for item in stored.items:
        item.preflight_ok = True
    uow.orders.save_order(stored, expected_version=stored.version)
    res = call(uow, "GET", "/v2/agent/queue", token=token, query="store_id=OSP")
    assert res.body["data"]["count"] == 1


def test_an_uncertain_attempt_parks_the_order_and_is_not_retryable(uow):
    token = login(uow)
    order = make_order(uow, token)
    call(uow, "POST", f"/v2/orders/{order['order_id']}/accept", {}, token=token)
    call(uow, "POST", f"/v2/orders/{order['order_id']}/payments",
         {"amount_paise": order["total_paise"], "method": "upi", "reference": "U2"}, token=token)
    stored = uow.orders.get_order(order["order_id"])
    for item in stored.items:
        item.preflight_ok = True
    uow.orders.save_order(stored, expected_version=stored.version)

    claim = call(uow, "POST", f"/v2/orders/{order['order_id']}/claim", {}, token=token)
    attempt_token = claim.body["data"]["attempt_token"]
    opened = call(uow, "POST", "/v2/attempts", {
        "order_id": order["order_id"], "attempt_token": attempt_token,
        "printer_queue": "Konica", "sheets_expected": 12,
    }, token=token)
    attempt_id = opened.body["data"]["attempt"]["attempt_id"]

    closed = call(uow, "POST", f"/v2/attempts/{attempt_id}/close", {
        "order_id": order["order_id"], "state": "uncertain",
        "failure_reason": "SumatraPDF timed out after spooling",
    }, token=token)
    assert closed.body["data"]["retryable"] is False
    assert closed.body["data"]["order"]["production_state"] == "output_uncertain"
    assert call(uow, "GET", "/v2/agent/queue", token=token,
                query="store_id=OSP").body["data"]["count"] == 0


# ── The compatibility guarantee ──────────────────────────────────────────────

class FakeHandler:
    """Just enough of BaseHTTPRequestHandler for dispatch() to inspect."""

    def __init__(self, method, path):
        self.command = method
        self.path = path
        self.headers = {}
        self.written = []
        self.status = None

    def send_response(self, status): self.status = status
    def send_header(self, *a): pass
    def end_headers(self): pass

    @property
    def wfile(self):
        handler = self

        class Sink:
            def write(self, data): handler.written.append(data)

        return Sink()


@pytest.mark.parametrize("path", [
    "/", "/health", "/order/quote", "/order/create", "/whatsapp-webhook",
    "/webhook/razorpay", "/staff/login", "/admin/send", "/academic/orders",
    "/cron/sla-check", "/project-builder/process", "/notes/submit", "/auth/wa-otp/request",
])
def test_dispatch_never_claims_a_legacy_path(path):
    """Every existing endpoint must fall straight through to the old chain."""
    app.reset_state()
    for method in ("GET", "POST"):
        assert app.dispatch(FakeHandler(method, path), b"") is False, f"{method} {path}"


def test_dispatch_answers_a_v2_path():
    app.reset_state()
    handler = FakeHandler("GET", "/v2/health")
    assert app.dispatch(handler, b"") is True
    assert handler.status in (200, 503)
    assert handler.written


def test_dispatch_never_raises_even_on_a_broken_request():
    """api/index.py calls this unguarded; a v2 defect must not break WhatsApp."""
    app.reset_state()

    class Hostile:
        command = "GET"
        path = "/v2/health"

        @property
        def headers(self):
            raise RuntimeError("headers exploded")

    assert app.dispatch(Hostile(), b"") is False
