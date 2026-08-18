"""
The fail-loud rule, as wired into the real pipelines.

test_ops_watchdog.py covers the watchdog itself; this file covers the wiring —
that the paths which swallowed the August 2026 Nattika outage now report. Each
test names the silence it closes.
"""

import os
import sys
import types
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_STUBS = [
    "gspread", "google", "google.auth", "google.auth.transport",
    "google.auth.transport.requests", "google.oauth2", "google.oauth2.service_account",
    "websockets", "pysnmp", "pysnmp.hlapi",
    "watchdog", "watchdog.observers", "watchdog.events", "razorpay",
]
for _mod in _STUBS:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

import pytest

import ops_watchdog as ow
import printer_poller as pp
import supabase_sync as ss


@pytest.fixture
def alerts(tmp_path, monkeypatch):
    sent = []
    ow.set_db_path(str(tmp_path / "jobs.db"))
    ow.reset()
    monkeypatch.setattr(ow, "_notify", lambda msg: sent.append(msg) or True)
    monkeypatch.setattr(ow, "_store_id", lambda: "PRINTK")
    yield sent
    ow.set_db_path(None)
    ow._memory.clear()


# ── The probe that was missing when the Epson's IP changed ────────────────────

def test_unreachable_printer_alerts(alerts, monkeypatch):
    monkeypatch.setattr(pp, "KONICA_IP", "")           # Nattika: no Konica
    monkeypatch.setattr(pp, "EPSON_IP", "192.168.1.201")
    monkeypatch.setattr(pp, "_printer_reachable", lambda ip, **k: False)

    pp._probe_and_report()

    assert len(alerts) == 1
    assert "printer.epson" in alerts[0]
    assert "192.168.1.201" in alerts[0]
    assert "IP changed" in alerts[0]


def test_a_store_with_no_konica_does_not_alert_about_one(alerts, monkeypatch):
    monkeypatch.setattr(pp, "KONICA_IP", "")
    monkeypatch.setattr(pp, "EPSON_IP", "192.168.1.250")
    monkeypatch.setattr(pp, "_printer_reachable", lambda ip, **k: True)

    result = pp._probe_and_report()

    assert result == {"epson": True}
    assert alerts == []


def test_reachable_printers_are_silent_then_loud_on_the_first_failure(alerts, monkeypatch):
    monkeypatch.setattr(pp, "KONICA_IP", "192.168.55.110")
    monkeypatch.setattr(pp, "EPSON_IP", "192.168.55.214")
    monkeypatch.setattr(pp, "_printer_reachable", lambda ip, **k: True)
    pp._probe_and_report()
    assert alerts == []

    monkeypatch.setattr(pp, "_printer_reachable", lambda ip, **k: ip != "192.168.55.214")
    pp._probe_and_report()
    assert len(alerts) == 1 and "printer.epson" in alerts[0]


def test_a_missing_ip_in_config_is_itself_a_failure(alerts, monkeypatch):
    monkeypatch.setattr(pp, "KONICA_IP", "192.168.55.110")
    monkeypatch.setattr(pp, "EPSON_IP", "")
    monkeypatch.setattr(pp, "_printer_reachable", lambda ip, **k: True)

    pp._probe_and_report()

    assert any("no epson_ip configured" in a for a in alerts)


# ── The sync whose health nobody ever read ────────────────────────────────────

def test_a_failed_sync_alerts_that_the_console_is_now_stale(alerts):
    ss._record_sync_failure("one or more table upserts failed")
    assert len(alerts) == 1
    assert "sync.supabase" in alerts[0]
    assert "stale" in alerts[0]


def test_sync_recovery_is_announced(alerts):
    ss._record_sync_failure("boom")
    ss._record_sync_success()
    assert "recovered" in alerts[-1]


# ── The Tier-1 fetcher that switched itself off "for this session" ────────────

def test_weblog_cools_down_instead_of_giving_up_forever():
    """It used to set _weblog_available=False permanently; a store PC runs for
    weeks, so 'this session' meant 'until someone notices'."""
    import epson_jobs_fetcher as ejf
    assert ejf._WEBLOG_RETRY_SECONDS > 0
    src = open(os.path.join(os.path.dirname(__file__), "..", "epson_jobs_fetcher.py"),
               encoding="utf-8").read()
    assert "_weblog_retry_at = time.monotonic() + _WEBLOG_RETRY_SECONDS" in src
    assert "cooldown elapsed — retrying Tier 1" in src


def test_configured_epson_ip_is_used_verbatim(monkeypatch):
    """The old silent remap (a configured .204 became .201) meant the fetcher
    could be talking to a different printer than the config named."""
    import epson_jobs_fetcher as ejf

    class _P:
        epson_ip = "192.168.1.204"
        konica_ip = None

    monkeypatch.setattr(ejf, "get_store_config",
                        lambda: types.SimpleNamespace(printers=_P()))
    assert ejf.get_epson_ip() == "192.168.1.204"


# ── Health is exposed where the consoles can read it ──────────────────────────

def test_print_server_status_and_health_carry_the_watchdog(alerts):
    src = open(os.path.join(os.path.dirname(__file__), "..", "print_server.py"),
               encoding="utf-8").read()
    assert '"watchdog": _watchdog_summary()' in src        # /status
    assert '"watchdog":     watchdog,' in src              # /health


def test_health_snapshot_names_what_is_broken(alerts, monkeypatch):
    monkeypatch.setattr(pp, "KONICA_IP", "")
    monkeypatch.setattr(pp, "EPSON_IP", "192.168.1.201")
    monkeypatch.setattr(pp, "_printer_reachable", lambda ip, **k: False)
    pp._probe_and_report()

    h = ow.health()
    assert h["healthy"] is False
    assert h["failing"] == ["printer.epson"]
    assert "192.168.1.201" in h["checks"]["printer.epson"]["detail"]


# ── A shared printer is polled by exactly one machine ─────────────────────────

def test_a_non_polling_box_does_not_poll_or_alert(alerts, monkeypatch, tmp_path):
    """The Nattika office box shares the counter's Epson. It must stay silent:
    not polling is a configuration choice, not a failure."""
    monkeypatch.setattr(pp, "POLL_PRINTERS", False)
    probed = []
    monkeypatch.setattr(pp, "_printer_reachable", lambda ip, **k: probed.append(ip) or False)

    pp.poll_once(str(tmp_path / "jobs.db"))

    assert probed == [], "a box that does not own the printers must not probe them"
    assert alerts == []
    assert pp.start_poller(str(tmp_path / "jobs.db")) is None


def test_a_non_polling_box_does_not_import_the_epson_job_log(monkeypatch, tmp_path):
    """This is what stored all 388 Nattika printer jobs twice."""
    import epson_jobs_fetcher as ejf
    monkeypatch.setattr(ejf, "POLL_PRINTERS", False)
    assert ejf.start_fetcher(str(tmp_path / "jobs.db")) is None


def test_the_polling_box_still_polls(alerts, monkeypatch, tmp_path):
    monkeypatch.setattr(pp, "POLL_PRINTERS", True)
    monkeypatch.setattr(pp, "KONICA_IP", "")
    monkeypatch.setattr(pp, "EPSON_IP", "192.168.1.250")
    monkeypatch.setattr(pp, "_printer_reachable", lambda ip, **k: False)
    monkeypatch.setattr(pp, "is_store_open", lambda *a, **k: False)

    pp.poll_once(str(tmp_path / "jobs.db"))      # closed + unreachable: skips the poll

    assert any("printer.epson" in a for a in alerts), "the owner of the printer must still alert"
