"""A store PC that quietly stops taking updates must alert.

OSP ran code from 21 August for eight days without a single alert. The boot
chain does report a failed pull to ops_watchdog as `store_pc.boot_update` — but
it only runs from BOOT_PRINTOSKY.bat, and OSP is started by hand with
START_PRINTOSKY.bat, which contains no git at all. A box without the boot chain
reports nothing, so nothing alerts: silence by construction, the same shape as
the Nattika outage.

The cloud could always see it — every box reports its build to
store_devices.app_version — so this is the rule that reads it.
"""
import pathlib

import pytest

import app_version as av

ROOT = pathlib.Path(__file__).resolve().parent.parent
CURRENT = "8037425abcdef1234567890abcdef1234567890a"
OLD = "main@558116d+dirty"


def decide(reported=OLD, current=CURRENT, hours=200.0, threshold=36.0, alerted=None):
    return av.decide_build_staleness(
        reported=reported, current=current, version_since_hours=hours,
        stale_after_hours=threshold, already_alerted=alerted)


# ── reading a reported version ───────────────────────────────────────────────

@pytest.mark.parametrize("reported,expected", [
    ("main@558116d+dirty", "558116d"),
    ("main@558116d", "558116d"),
    ("558116d", "558116d"),
    ("unknown", None),
    ("", None),
    (None, None),
])
def test_short_sha(reported, expected):
    assert av.short_sha(reported) == expected


def test_a_box_matches_the_deploy_on_the_shorter_prefix():
    """Boxes report 7 characters; VERCEL_GIT_COMMIT_SHA carries all 40."""
    assert av.same_build("main@8037425+dirty", CURRENT)
    assert not av.same_build("main@558116d", CURRENT)


def test_a_dirty_tree_still_counts_as_the_build_it_is_on():
    """+dirty says someone hand-patched the box — worth seeing, but it is not
    the same failure as running last week's code."""
    assert av.same_build("main@8037425+dirty", CURRENT)


# ── the rule ─────────────────────────────────────────────────────────────────

def test_a_box_on_the_current_build_is_never_stale():
    v = decide(reported="main@8037425")
    assert not v["stale"] and not v["alert"]


def test_a_box_behind_for_days_alerts():
    v = decide()
    assert v["stale"] and v["alert"]
    assert "558116d" in v["reason"] and "8037425" in v["reason"]


def test_a_box_behind_only_since_this_morning_does_not_alert():
    """A box updates at boot. Being behind between a push and the next morning
    is the normal state of every store, not an outage."""
    v = decide(hours=5.0)
    assert not v["stale"] and not v["alert"]


def test_the_same_stale_build_alerts_only_once():
    """Dedup, so the 6-hourly cron does not send the same alert four times a day."""
    first = decide()
    assert first["alert"]
    again = decide(alerted=first["key"])
    assert again["stale"] and not again["alert"]


def test_a_box_that_moves_to_a_different_stale_build_alerts_again():
    v = decide(reported="main@b37b008", alerted="558116d")
    assert v["stale"] and v["alert"]


def test_recovery_is_announced_once_the_box_catches_up():
    v = decide(reported="main@8037425", alerted="558116d")
    assert not v["stale"] and v["recovered"]


def test_recovery_is_not_announced_for_a_box_that_was_never_stale():
    assert not decide(reported="main@8037425", alerted=None)["recovered"]


def test_a_box_that_cannot_report_its_version_is_flagged():
    """`unknown` means git is missing or the checkout is broken — that box's
    build is unverifiable, which is its own problem."""
    v = decide(reported="unknown")
    assert v["stale"] and v["alert"]
    assert "not reporting" in v["reason"]


def test_a_missing_deploy_sha_reports_itself_rather_than_going_quiet():
    """If VERCEL_GIT_COMMIT_SHA is absent the check cannot run. Saying nothing
    would recreate exactly the silence this exists to break."""
    v = decide(current=None)
    assert not v["stale"] and v["alert"]
    assert "VERCEL_GIT_COMMIT_SHA" in v["reason"]
    assert not decide(current=None, alerted="unknown-current")["alert"]


def test_an_unknown_version_with_no_history_still_alerts():
    """A box with no app_version_since (never seen changing) must not slip
    through as 'not stale yet'."""
    v = decide(hours=None)
    assert v["stale"] and v["alert"]


# ── wiring ───────────────────────────────────────────────────────────────────

def test_the_cron_runs_the_check():
    src = (ROOT / "api" / "index.py").read_text(encoding="utf-8")
    assert "_check_build_freshness" in src
    assert "VERCEL_GIT_COMMIT_SHA" in src
    assert "stale_build_alerted" in src


def test_retired_devices_are_excluded():
    """The PRINTK row for the machine that became PRIOFF has not reported since
    19 Aug. A device that stopped reporting is retired, not stale, and must
    never alert."""
    src = (ROOT / "api" / "index.py").read_text(encoding="utf-8")
    assert "STORE_BUILD_DEVICE_MAX_AGE_H" in src
    assert "_live_devices" in src


def test_the_check_cannot_break_the_liveness_sweep():
    """Liveness is the older, more important signal; a bug here must not take
    it down."""
    src = (ROOT / "api" / "index.py").read_text(encoding="utf-8")
    call = src.index("stale_build = _check_build_freshness(")
    block = src[call - 200:call + 400]
    assert "try:" in block and "except Exception" in block


def test_the_migration_adds_both_halves():
    sql = (ROOT / "supabase" / "migrations"
           / "20260829140000_build_staleness.sql").read_text(encoding="utf-8")
    assert "app_version_since" in sql          # how long has it been on this build
    assert "stale_build_alerted" in sql        # what did we already alert about
    assert "CREATE TRIGGER" in sql             # stamped by the DB, not per-cycle reads
