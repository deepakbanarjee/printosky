"""
Telling Meta which ad click turned into money.

Ad 120248100283360186 ran 7-14 Sep 2026 and produced 8 clicks, 7 people and
Rs.0. Its objective was LINK_CLICKS and the only signal Meta ever got back was
the click, so it kept buying cheap clicks. Every one of those clicks carried a
`ctwa_clid` that SCHEMA v42 stored and nothing ever sent.

These tests pin the return path: the two fields that make an event a
click-to-WhatsApp conversion rather than a website one, the click id arriving
unhashed, and -- the part that actually decides whether this works in
production -- that a conversion we cannot send is loud rather than lost.
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

capi = pytest.importorskip("meta_capi", reason="meta_capi unavailable")

CLID = "ARAaBbCcDd_clickid"
PHONE = "919233255016"
ORDER = "OSP-20260916-0001"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("META_CAPI_DATASET_ID", "META_CAPI_TOKEN",
                "META_SYSTEM_USER_TOKEN", "META_PAGE_ID"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("META_CAPI_DATASET_ID", "1234567890")
    monkeypatch.setenv("META_SYSTEM_USER_TOKEN", "EAAtoken")


class TestTheEventShape:
    """An event missing action_source/messaging_channel is ACCEPTED by Meta and
    attributed to nobody — indistinguishable from never sending it."""

    def test_it_is_a_business_messaging_event_on_whatsapp(self):
        e = capi.build_event(CLID, ORDER, 290.0)
        assert e["action_source"] == "business_messaging"
        assert e["messaging_channel"] == "whatsapp"

    def test_the_click_id_is_not_hashed(self):
        """Every other user identifier in the Conversions API is SHA-256. This
        one is a click id Meta issued itself and must arrive verbatim."""
        assert capi.build_event(CLID, ORDER, 290.0)["user_data"]["ctwa_clid"] == CLID

    def test_the_order_id_is_the_dedup_key(self):
        """Razorpay fires the same payment.captured more than once."""
        assert capi.build_event(CLID, ORDER, 290.0)["event_id"] == ORDER

    def test_value_and_currency_ride_along(self):
        cd = capi.build_event(CLID, ORDER, 290.5)["custom_data"]
        assert cd == {"currency": "INR", "value": 290.5}

    def test_event_time_is_unix_seconds(self):
        e = capi.build_event(CLID, ORDER, 290.0, event_time=1758000000)
        assert e["event_time"] == 1758000000
        assert isinstance(capi.build_event(CLID, ORDER, 290.0)["event_time"], int)

    def test_page_id_only_when_configured(self, monkeypatch):
        assert "page_id" not in capi.build_event(CLID, ORDER, 290.0)["user_data"]
        monkeypatch.setenv("META_PAGE_ID", "999350999936953")
        assert capi.build_event(CLID, ORDER, 290.0)["user_data"]["page_id"] == "999350999936953"


class TestConfiguration:
    def test_both_halves_are_required(self, monkeypatch):
        assert capi.is_configured() is False
        monkeypatch.setenv("META_CAPI_DATASET_ID", "123")
        assert capi.is_configured() is False, "a dataset with no token sends nothing"
        monkeypatch.setenv("META_SYSTEM_USER_TOKEN", "EAAtoken")
        assert capi.is_configured() is True

    def test_the_whatsapp_token_is_the_default(self, monkeypatch):
        monkeypatch.setenv("META_CAPI_DATASET_ID", "123")
        monkeypatch.setenv("META_SYSTEM_USER_TOKEN", "EAAshared")
        assert capi._token() == "EAAshared"
        monkeypatch.setenv("META_CAPI_TOKEN", "EAAdedicated")
        assert capi._token() == "EAAdedicated", "an explicit CAPI token wins"


class _Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self._body = body if body is not None else {"events_received": 1}
        self.text = text

    def json(self):
        if self._body is _BAD_JSON:
            raise ValueError("not json")
        return self._body


_BAD_JSON = object()


@pytest.fixture
def http(monkeypatch):
    """Capture the outgoing request without making one."""
    calls: list[dict] = []
    outcome = {"resp": _Resp()}

    class _FakeRequests:
        @staticmethod
        def post(url, json=None, headers=None, timeout=None):
            calls.append({"url": url, "json": json, "headers": headers,
                          "timeout": timeout})
            if isinstance(outcome["resp"], Exception):
                raise outcome["resp"]
            return outcome["resp"]

    monkeypatch.setitem(sys.modules, "requests", _FakeRequests)
    return calls, outcome


class TestSendEvent:
    def test_it_posts_to_the_dataset_events_endpoint(self, configured, http):
        calls, _ = http
        ok, err, _trace = capi.send_event(CLID, ORDER, 290.0)
        assert ok is True and err == ""
        assert calls[0]["url"] == "https://graph.facebook.com/v21.0/1234567890/events"
        assert calls[0]["headers"]["Authorization"] == "Bearer EAAtoken"
        assert calls[0]["json"]["data"][0]["user_data"]["ctwa_clid"] == CLID

    def test_it_does_not_hang_the_payment_path(self, configured, http):
        calls, _ = http
        capi.send_event(CLID, ORDER, 290.0)
        assert calls[0]["timeout"], "a payment webhook must not wait on Meta forever"

    def test_an_http_error_is_reported_with_metas_reason(self, configured, http):
        _, outcome = http
        outcome["resp"] = _Resp(400, {"error": {"message": "Invalid dataset ID"},
                                      "fbtrace_id": "AbC123"})
        ok, err, trace = capi.send_event(CLID, ORDER, 290.0)
        assert ok is False
        assert "400" in err and "Invalid dataset ID" in err
        assert trace == "AbC123"

    def test_accepted_but_kept_nothing_is_a_failure(self, configured, http):
        """A 200 with events_received=0 is a rejection wearing a success's
        clothes. Counting it as sent is how a broken integration looks fine."""
        _, outcome = http
        outcome["resp"] = _Resp(200, {"events_received": 0})
        ok, err, _ = capi.send_event(CLID, ORDER, 290.0)
        assert ok is False and "events_received=0" in err

    def test_a_network_error_never_escapes(self, configured, http):
        _, outcome = http
        outcome["resp"] = ConnectionError("meta unreachable")
        ok, err, _ = capi.send_event(CLID, ORDER, 290.0)
        assert ok is False and "ConnectionError" in err

    def test_an_unparseable_body_is_not_a_crash(self, configured, http):
        _, outcome = http
        outcome["resp"] = _Resp(500, _BAD_JSON, text="upstream exploded")
        ok, err, _ = capi.send_event(CLID, ORDER, 290.0)
        assert ok is False and "500" in err

    def test_unconfigured_sends_nothing(self, http):
        calls, _ = http
        ok, err, _ = capi.send_event(CLID, ORDER, 290.0)
        assert ok is False and "DATASET_ID" in err
        assert calls == [], "no request without somewhere to send it"


@pytest.fixture
def wired(monkeypatch):
    """Stub the db_cloud seam report_purchase reaches through."""
    db_cloud = pytest.importorskip("db_cloud", reason="supabase SDK not installed")
    state = {"attribution": {"ctwa_clid": CLID, "source_id": "120248100283360186"},
             "exists": False, "recorded": [], "alerts": []}

    monkeypatch.setattr(db_cloud, "ctwa_clid_for_phone",
                        lambda p: state["attribution"])
    monkeypatch.setattr(db_cloud, "ad_conversion_exists",
                        lambda o: state["exists"])
    monkeypatch.setattr(db_cloud, "record_ad_conversion",
                        lambda **kw: state["recorded"].append(kw))
    monkeypatch.setattr(capi, "_alert",
                        lambda ok, detail: state["alerts"].append((ok, detail)))
    return state


class TestReportPurchase:
    def test_an_ad_customers_payment_is_reported_and_recorded(
            self, configured, http, wired):
        assert capi.report_purchase(PHONE, ORDER, 290.0) is True
        assert wired["recorded"][0]["ok"] is True
        assert wired["recorded"][0]["ctwa_clid"] == CLID
        assert wired["recorded"][0]["source_id"] == "120248100283360186"
        assert wired["alerts"] == [(True, f"reported {ORDER}")]

    def test_an_ordinary_customer_is_silent(self, configured, http, wired):
        """Most paying customers never clicked an ad. That is not news, and
        alerting on it would bury the failures that matter."""
        calls, _ = http
        wired["attribution"] = None
        assert capi.report_purchase(PHONE, ORDER, 290.0) is False
        assert calls == [] and wired["alerts"] == [] and wired["recorded"] == []

    def test_an_unsendable_conversion_alerts(self, configured, http, wired):
        _, outcome = http
        outcome["resp"] = _Resp(400, {"error": {"message": "Invalid dataset ID"}})
        assert capi.report_purchase(PHONE, ORDER, 290.0) is False
        assert wired["alerts"][0][0] is False
        assert "Invalid dataset ID" in wired["alerts"][0][1]

    def test_a_failure_is_recorded_with_its_reason(self, configured, http, wired):
        """The failures are the point of the table. A boolean 'sent' column
        would keep the successes and lose every conversion Meta refused."""
        _, outcome = http
        outcome["resp"] = _Resp(400, {"error": {"message": "Invalid dataset ID"}})
        capi.report_purchase(PHONE, ORDER, 290.0)
        rec = wired["recorded"][0]
        assert rec["ok"] is False and "Invalid dataset ID" in rec["error"]

    def test_an_attributable_conversion_with_no_config_is_loud(self, http, wired):
        """This is the bug being fixed, one layer out: a real conversion with a
        real click id, thrown away because nobody set the env var."""
        calls, _ = http
        assert capi.report_purchase(PHONE, ORDER, 290.0) is False
        assert calls == []
        assert wired["alerts"][0][0] is False
        assert "META_CAPI_DATASET_ID" in wired["alerts"][0][1]
        assert wired["recorded"][0]["error"] == "not configured"

    def test_an_already_reported_order_is_not_sent_twice(
            self, configured, http, wired):
        calls, _ = http
        wired["exists"] = True
        assert capi.report_purchase(PHONE, ORDER, 290.0) is False
        assert calls == [], "Razorpay fires the same payment.captured twice"

    def test_a_dedup_read_failure_still_sends(self, configured, http, wired,
                                              monkeypatch):
        """Meta dedups on event_id too, so a possible duplicate beats a
        conversion dropped because one read failed."""
        calls, _ = http
        db_cloud = sys.modules["db_cloud"]
        monkeypatch.setattr(db_cloud, "ad_conversion_exists",
                            lambda o: (_ for _ in ()).throw(RuntimeError("down")))
        assert capi.report_purchase(PHONE, ORDER, 290.0) is True
        assert len(calls) == 1

    def test_an_attribution_lookup_failure_is_loud(self, configured, http, wired,
                                                   monkeypatch):
        """Staying quiet here is exactly how an attributable conversion goes
        missing — we do not know that this was not an ad customer."""
        db_cloud = sys.modules["db_cloud"]
        monkeypatch.setattr(db_cloud, "ctwa_clid_for_phone",
                            lambda p: (_ for _ in ()).throw(RuntimeError("down")))
        assert capi.report_purchase(PHONE, ORDER, 290.0) is False
        assert wired["alerts"][0][0] is False

    def test_a_recording_failure_does_not_mask_the_send(
            self, configured, http, wired, monkeypatch):
        db_cloud = sys.modules["db_cloud"]
        monkeypatch.setattr(db_cloud, "record_ad_conversion",
                            lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))
        assert capi.report_purchase(PHONE, ORDER, 290.0) is True

    @pytest.mark.parametrize("phone,order", [("", ORDER), (PHONE, ""), ("", "")])
    def test_missing_identifiers_are_declined_quietly(
            self, configured, http, wired, phone, order):
        calls, _ = http
        assert capi.report_purchase(phone, order, 290.0) is False
        assert calls == []


class _FakeQuery:
    """Chainable stand-in for the Supabase query builder, recording filters."""

    def __init__(self, rows, filters):
        self._rows = rows
        self.filters = filters

    def select(self, *_a, **_kw):
        return self

    def limit(self, *_a, **_kw):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class TestRetryAfterFailure:
    """A failed attempt leaves a row. If that row counted as "reported", the
    first failure would retire the conversion for good — and the failed rows
    are supposed to BE the retry queue."""

    def _fake_client(self, monkeypatch, rows):
        db_cloud = sys.modules["db_cloud"]
        filters: list = []
        monkeypatch.setattr(db_cloud, "_client",
                            lambda: type("C", (), {
                                "table": lambda _s, _n: _FakeQuery(rows, filters)
                            })())
        return filters

    def test_only_a_successful_send_counts_as_reported(self, monkeypatch):
        db_cloud = sys.modules["db_cloud"]
        filters = self._fake_client(monkeypatch, [])
        assert db_cloud.ad_conversion_exists(ORDER) is False
        assert ("ok", True) in filters, (
            "without this filter a failed row blocks its own retry forever")
        assert ("order_id", ORDER) in filters

    def test_a_reported_order_is_found(self, monkeypatch):
        db_cloud = sys.modules["db_cloud"]
        self._fake_client(monkeypatch, [{"id": 1}])
        assert db_cloud.ad_conversion_exists(ORDER) is True

    def test_no_order_id_never_queries(self, monkeypatch):
        db_cloud = sys.modules["db_cloud"]
        filters = self._fake_client(monkeypatch, [{"id": 1}])
        assert db_cloud.ad_conversion_exists("") is False
        assert filters == []
