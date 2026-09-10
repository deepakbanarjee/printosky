r"""
A test run must never touch a real store's health state.

`ops_watchdog` resolves its SQLite file from `_db_path_override`, then
`$PRINTOSKY_DB`, then the store config — and on a store PC that last one is the
live `C:\Printosky\Data\jobs.db`. Only two test files ever called
`set_db_path`, so every other test that tripped a `report()` wrote into
whichever store it happened to run on.

Observed on PRIOFF, 2026-09-10: after one suite run, `print_server /health` was
red with four failures the shop did not have, including one naming a
`pytest-of-user\pytest-45\...` path and a store id from a fixture.

The banner is the small half. `report()` also stamps `last_alert_at`, and the
dedup logic reads it — so a genuine failure arriving within the repeat window
is silently swallowed as "alert already sent". A test run could mute a real
store's alerts for six hours. These tests pin the fix in conftest.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ops_watchdog as ow


def test_the_suite_points_the_watchdog_at_a_temp_db():
    override = ow._db_path_override
    assert override, (
        "conftest must call ops_watchdog.set_db_path() — without it a suite run "
        "on a store PC writes into that store's live ops_health table"
    )
    lowered = override.lower()
    assert "pytest" in lowered or "tmp" in lowered or "temp" in lowered, override


def test_the_resolved_db_is_the_temp_one_not_the_store_default():
    """The override only helps if it actually wins the resolution order."""
    resolved = ow._db_path()
    assert resolved == ow._db_path_override, (
        f"watchdog resolved to {resolved!r}, not the test override "
        f"{ow._db_path_override!r}"
    )


def test_the_suite_cannot_send_a_real_alert():
    """A test that trips a check must not reach WhatsApp. Tests that assert on
    alerts patch `_notify` wholesale, so this flag never gets in their way — it
    only stops the ones that were never thinking about alerts at all."""
    assert ow.ALERTS_ENABLED is False


def test_a_report_lands_in_the_temp_db(tmp_path):
    """The guarantee, exercised rather than asserted about."""
    import sqlite3

    ow.report("test.isolation_probe", False, "written by the test suite")

    with sqlite3.connect(ow._db_path_override) as c:
        row = c.execute(
            "SELECT detail FROM ops_health WHERE check_name = ?",
            ("test.isolation_probe",)).fetchone()
    assert row and row[0] == "written by the test suite"
