"""Every timestamp and every dated id comes from one clock: the shop's.

`datetime.now()` returns IST on a store PC and UTC on Vercel, and Printosky
wrote both into the same places. On 2026-09-06 both halves were visible in one
screen: a counter job read `13:22:32` and a web job `07:53:03`, thirty-one
seconds apart in the shop and five and a half hours apart in the column.

Three things inherited the split, not one:

* `jobs.received_at` — `docs/SCHEMA.md` calls it "ISO-8601 string (legacy from
  SQLite)" and names no zone, which is exactly why two writers guessed
  differently.
* **Date-stamped identifiers.** `OSKY-20260905-…` is built from
  `datetime.now()` too, so a job taken at 02:00 IST carried yesterday's date in
  the number the customer is quoted.
* **Range reads.** `api.index._sd_jobs_range()` bounds that column with plain
  string comparison, so a cloud-written job received between 00:00 and 05:30
  IST lands on the previous day's takings.

These tests are a ratchet, the same shape as `test_fail_loud_rule.py`: the fix
is one-line-per-site and would be one line to undo, so the rule is asserted
rather than remembered.
"""

import ast
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

import clock


# ── the clock itself ─────────────────────────────────────────────────────────

def test_the_zone_is_asia_kolkata():
    assert clock.IST.utcoffset(None) == timedelta(hours=5, minutes=30)


def test_it_does_not_need_tzdata_on_windows():
    """A fixed offset, not `zoneinfo`. `zoneinfo` needs the tzdata package
    present on Windows, and the store PCs are the one place this must never
    fail to import. IST has no DST, so the offset is exact."""
    src = (ROOT / "clock.py").read_text(encoding="utf-8-sig")
    assert "import zoneinfo" not in src and "from zoneinfo" not in src
    assert "timezone(timedelta(hours=5, minutes=30)" in src


def test_now_str_is_the_shape_the_column_takes():
    """Space-separated, not 'T'. `_sd_jobs_range()` compares this as a string,
    so the format is load-bearing."""
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", clock.now_str())


def test_today_str_is_the_business_date_not_the_servers():
    """02:00 IST is 20:30 UTC the day before. The job id must read as the day
    the shop was open, which is the whole reason for choosing IST over UTC."""
    two_am_ist = datetime(2026, 9, 6, 2, 0, tzinfo=clock.IST)
    assert two_am_ist.astimezone(timezone.utc).strftime("%Y%m%d") == "20260905"
    assert two_am_ist.strftime(clock.DATE_FORMAT) == "20260906"


def test_a_naive_datetime_is_read_as_utc():
    """Everything naive in this codebase that is not already IST came out of a
    cloud container."""
    assert clock.to_ist(datetime(2026, 9, 6, 7, 53, 3)).hour == 13


def test_an_aware_datetime_is_respected_not_reinterpreted():
    already = datetime(2026, 9, 6, 13, 22, 32, tzinfo=clock.IST)
    assert clock.to_ist(already) == already


# ── the ratchet: no bare clock reads where they matter ───────────────────────

#: Files that write a timestamp column or mint a dated identifier. Each one
#: straddles the store PC / Vercel split that caused this.
GOVERNED = [
    "db_cloud.py",
    "watcher.py",
    "api/index.py",
    "api/handlers_order.py",
    "api/handlers_admin.py",
]

#: `datetime.now()` with no argument. `datetime.now(tz)` is fine — it says which
#: clock it means, which is the entire point.
_BARE_NOW = re.compile(r"datetime\.now\(\s*\)")
_UTCNOW = re.compile(r"datetime\.utcnow\(\s*\)")

#: What a bare read is being used FOR. A bare `datetime.now()` for a log line or
#: an elapsed-time measurement is harmless; one that becomes a stored timestamp
#: or a customer-facing id is the bug.
_DANGEROUS_USE = re.compile(r"%Y-%m-%d %H:%M:%S|%Y%m%d")


def _offending_lines(path: Path) -> list[tuple[int, str]]:
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        if (_BARE_NOW.search(line) or _UTCNOW.search(line)) and _DANGEROUS_USE.search(line):
            out.append((i, line.strip()))
    return out


@pytest.mark.parametrize("relpath", GOVERNED)
def test_no_bare_clock_read_becomes_a_timestamp_or_an_id(relpath):
    offenders = _offending_lines(ROOT / relpath)
    assert not offenders, (
        f"{relpath} formats a bare datetime.now()/utcnow() into a timestamp or "
        "a dated id. That reads the clock of whichever machine runs it — IST on "
        "a store PC, UTC on Vercel — which is the split this module exists to "
        "close. Use clock.now_str() or clock.today_str():\n"
        + "\n".join(f"  line {n}: {t}" for n, t in offenders)
    )


def test_the_ratchet_can_actually_fail(tmp_path):
    """A rule that cannot fail is not a rule. Three faults in this run were a
    green light computed over nothing, so prove the detector on a known-bad
    sample before trusting its silence."""
    bad = tmp_path / "bad.py"
    bad.write_text(
        'x = {"received_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n'
        "job_id = f\"OSKY-{datetime.utcnow().strftime('%Y%m%d')}-0001\"\n",
        encoding="utf-8",
    )
    assert len(_offending_lines(bad)) == 2

    ok = tmp_path / "ok.py"
    ok.write_text(
        'x = {"received_at": clock.now_str()}\n'
        'elapsed = (datetime.now() - started).total_seconds()\n'
        'logger.info("at %s", datetime.now())\n',
        encoding="utf-8",
    )
    assert _offending_lines(ok) == [], (
        "the ratchet flags a bare now() used for elapsed time or a log line — "
        "those are harmless, and a rule that cries wolf gets switched off"
    )


@pytest.mark.parametrize("relpath", GOVERNED)
def test_every_governed_file_actually_uses_the_clock(relpath):
    """Guards against the file being renamed or emptied and this suite quietly
    passing over nothing."""
    src = (ROOT / relpath).read_text(encoding="utf-8-sig")
    assert "clock." in src, (
        f"{relpath} no longer references clock at all — either it stopped "
        "writing timestamps, in which case take it out of GOVERNED, or the "
        "import was dropped and every site here silently regressed"
    )


def test_the_governed_list_is_not_empty():
    assert GOVERNED
