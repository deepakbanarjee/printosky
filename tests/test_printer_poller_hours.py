"""Tests for hours-aware printer polling.

An unreachable printer during open hours is a real fault; outside open hours it
just means the shop is closed and the printers are powered off. The poller skips
quietly when closed+unreachable, but still polls if someone is working late and
the printers are on. See multistore-pivot "Store-tier model & metering".
"""
from datetime import datetime

import printer_poller as pp


class TestIsStoreOpen:
    # Oxygen: Mon–Sat 08:00–19:00, Sunday closed.
    # 2026-06-08 is a Monday; 2026-06-13 a Saturday; 2026-06-07 a Sunday.

    def test_open_weekday_midday(self):
        assert pp.is_store_open(datetime(2026, 6, 8, 10, 0)) is True

    def test_before_open_is_closed(self):
        assert pp.is_store_open(datetime(2026, 6, 8, 7, 59)) is False

    def test_open_hour_is_inclusive(self):
        assert pp.is_store_open(datetime(2026, 6, 8, 8, 0)) is True

    def test_last_open_minute(self):
        assert pp.is_store_open(datetime(2026, 6, 8, 18, 59)) is True

    def test_close_hour_is_exclusive(self):
        # 19:00 == 7pm is closed; close hour is exclusive.
        assert pp.is_store_open(datetime(2026, 6, 8, 19, 0)) is False

    def test_saturday_open(self):
        assert pp.is_store_open(datetime(2026, 6, 13, 10, 0)) is True

    def test_sunday_closed(self):
        assert pp.is_store_open(datetime(2026, 6, 7, 10, 0)) is False


class TestPollOnceHoursAware:
    def _patch_polls(self, monkeypatch, calls):
        """Replace every real poll function with a recorder so we can tell
        whether any actual polling work was attempted."""
        def mark(name, ret=None):
            def _f(*a, **k):
                calls.append(name)
                return ret
            return _f
        monkeypatch.setattr(pp, "poll_konica_xml", mark("konica_xml"))
        monkeypatch.setattr(pp, "poll_konica_snmp", mark("konica_snmp"))
        monkeypatch.setattr(pp, "poll_konica_supplies_xml", mark("ksup_xml", []))
        monkeypatch.setattr(pp, "poll_konica_supplies_vendor_snmp", mark("ksup_snmp", []))
        monkeypatch.setattr(pp, "poll_epson_web", mark("epson_web"))
        monkeypatch.setattr(pp, "poll_epson_snmp", mark("epson_snmp"))
        monkeypatch.setattr(pp, "poll_supplies", mark("supplies", []))
        monkeypatch.setattr(pp, "_send_ink_alerts", lambda *a, **k: None)

    def test_skips_when_closed_and_unreachable(self, monkeypatch, tmp_path):
        calls = []
        self._patch_polls(monkeypatch, calls)
        monkeypatch.setattr(pp, "is_store_open", lambda *a, **k: False)
        monkeypatch.setattr(pp, "_any_printer_reachable", lambda: False)
        pp.poll_once(str(tmp_path / "jobs.db"))
        assert calls == []  # nothing polled — quiet skip

    def test_polls_when_closed_but_reachable(self, monkeypatch, tmp_path):
        # Working late: clock says closed, but the printers are powered on.
        calls = []
        self._patch_polls(monkeypatch, calls)
        monkeypatch.setattr(pp, "is_store_open", lambda *a, **k: False)
        monkeypatch.setattr(pp, "_any_printer_reachable", lambda: True)
        pp.poll_once(str(tmp_path / "jobs.db"))
        assert "konica_xml" in calls and "epson_web" in calls

    def test_polls_when_open_without_probing(self, monkeypatch, tmp_path):
        # When open, reachability must not even be probed (short-circuit).
        calls = []
        self._patch_polls(monkeypatch, calls)
        monkeypatch.setattr(pp, "is_store_open", lambda *a, **k: True)

        def _boom():
            raise AssertionError("reachability should not be probed when open")
        monkeypatch.setattr(pp, "_any_printer_reachable", _boom)
        pp.poll_once(str(tmp_path / "jobs.db"))
        assert "konica_xml" in calls


class TestPrinterReachable:
    def test_unreachable_host_returns_false(self):
        # TEST-NET-1 (192.0.2.0/24, RFC 5737) is guaranteed unroutable.
        assert pp._printer_reachable("192.0.2.1", port=80, timeout=0.5) is False
