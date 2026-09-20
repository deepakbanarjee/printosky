"""
The SQL in SCHEMA_v44 must agree with the Python it replaces.

db_cloud._compute_sla_breaches() is the reference implementation: it is pure,
already covered by tests, and stays in the tree as find_sla_breaches's fallback
for a deployment where the migration has not been applied yet. This file runs
BOTH over the same rows — the Python in process, the SQL in a real Postgres —
and asserts identical output, on hand-built edge cases and on randomised ones.

That equivalence is the whole safety argument for the change. A silent
divergence here means a customer waiting with nobody alerted, which is what
docs/FAIL_LOUD.md exists to prevent, so this is checked rather than reasoned
about.

Skipped when no Postgres is reachable (CI installs none today). Point
PRINTOSKY_TEST_PG at a database to run it:

    PRINTOSKY_TEST_PG='host=/tmp port=55432 user=postgres dbname=sla_test'
"""

import os
import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

_MIGRATION = os.path.join(
    os.path.dirname(__file__), "..", "api", "migrations",
    "SCHEMA_v44_sla_breaches_rpc.sql",
)

_SCHEMA = """
DROP TABLE IF EXISTS public.conversation_log CASCADE;
CREATE TABLE public.conversation_log (
    id            BIGSERIAL PRIMARY KEY,
    phone         TEXT NOT NULL,
    direction     TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    message_type  TEXT NOT NULL DEFAULT 'text',
    body          TEXT,
    filename      TEXT,
    job_id        TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
DO $$ BEGIN CREATE ROLE service_role; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
"""


def _psql(sql, dsn, want_rows=False):
    cmd = ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-X", "-q"]
    if want_rows:
        cmd += ["-t", "-A", "-F", "\x1f"]
    r = subprocess.run(cmd, input=sql, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()}\n--- sql ---\n{sql[:2000]}")
    return r.stdout


@pytest.fixture(scope="module")
def dsn():
    d = os.environ.get("PRINTOSKY_TEST_PG")
    if not d:
        pytest.skip("PRINTOSKY_TEST_PG not set — no Postgres to verify the SQL against")
    try:
        _psql("SELECT 1;", d)
    except Exception as exc:
        pytest.skip(f"Postgres not reachable: {exc}")
    _psql(_SCHEMA, d)
    with open(_MIGRATION, encoding="utf-8") as fh:
        _psql(fh.read(), d)
    return d


@pytest.fixture
def run_both(dsn):
    """Load `rows` into Postgres, run both implementations, return (sql, python).

    Each row is (phone, direction, minutes_ago). `now` is Postgres's own now(),
    read back so both sides measure the window from the same instant.
    """
    from db_cloud import _compute_sla_breaches

    def _go(rows, threshold_hours=1, tolerance_seconds=120, lookback_hours=48):
        now_s = _psql("SELECT now();", dsn, want_rows=True).strip()
        now = datetime.fromisoformat(now_s)

        _psql("TRUNCATE public.conversation_log;", dsn)
        if rows:
            values = ",".join(
                "('%s','%s', now() - interval '%f minutes')" % (p, d, m)
                for p, d, m in rows
            )
            _psql(
                "INSERT INTO public.conversation_log (phone, direction, created_at) "
                f"VALUES {values};", dsn
            )

        out = _psql(
            "SELECT phone, last_inbound_at FROM public.sla_breaches"
            f"({threshold_hours}, {tolerance_seconds}, {lookback_hours});",
            dsn, want_rows=True,
        )
        sql_phones = sorted(
            line.split("\x1f")[0] for line in out.splitlines() if line.strip()
        )

        # _compute_sla_breaches has no lookback of its own — find_sla_breaches
        # bounds it with .gte("created_at", cutoff) on the query. So the window
        # is applied to the ROWS here, before reducing, which is what the real
        # caller does. (Filtering the resulting breaches instead is wrong: a
        # phone can have one inbound outside the window and a newer one inside,
        # and it is the newest that decides.)
        window_start = now - timedelta(hours=lookback_hours)
        py_rows = [
            {"phone": p, "direction": d,
             "created_at": (now - timedelta(minutes=m)).isoformat()}
            for p, d, m in rows
            if now - timedelta(minutes=m) >= window_start
        ]
        py_phones = sorted(
            b["phone"] for b in _compute_sla_breaches(
                py_rows, now, threshold_hours,
                reply_tolerance_seconds=tolerance_seconds)
        )
        return sql_phones, py_phones

    return _go


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases, stated as behaviour
# ─────────────────────────────────────────────────────────────────────────────

class TestAgreementOnEdgeCases:
    def test_unanswered_old_inbound_is_a_breach(self, run_both):
        sql, py = run_both([("+911", "inbound", 180)])
        assert sql == py == ["+911"]

    def test_answered_inbound_is_not(self, run_both):
        sql, py = run_both([("+911", "inbound", 180), ("+911", "outbound", 170)])
        assert sql == py == []

    def test_recent_inbound_is_not_yet_a_breach(self, run_both):
        """Newer than threshold_hours — still inside the promise."""
        sql, py = run_both([("+911", "inbound", 10)])
        assert sql == py == []

    def test_reply_older_than_the_inbound_does_not_count(self, run_both):
        """They wrote again after our last reply."""
        sql, py = run_both([("+911", "outbound", 300), ("+911", "inbound", 180)])
        assert sql == py == ["+911"]

    def test_reply_just_inside_tolerance_counts_as_answered(self, run_both):
        """A reply logged a moment BEFORE the inbound it answers still counts."""
        sql, py = run_both([("+911", "inbound", 180), ("+911", "outbound", 181)],
                           tolerance_seconds=120)
        assert sql == py == []

    def test_reply_just_outside_tolerance_does_not(self, run_both):
        sql, py = run_both([("+911", "inbound", 180), ("+911", "outbound", 184)],
                           tolerance_seconds=120)
        assert sql == py == ["+911"]

    def test_outbound_only_phone_is_never_a_breach(self, run_both):
        sql, py = run_both([("+911", "outbound", 300)])
        assert sql == py == []

    def test_empty_table(self, run_both):
        sql, py = run_both([])
        assert sql == py == []

    def test_many_phones_are_judged_independently(self, run_both):
        sql, py = run_both([
            ("+911", "inbound", 180),                                  # breach
            ("+912", "inbound", 180), ("+912", "outbound", 170),       # answered
            ("+913", "inbound", 5),                                    # too recent
            ("+914", "outbound", 200),                                 # no inbound
            ("+915", "inbound", 600), ("+915", "outbound", 900),       # stale reply
        ])
        assert sql == py == ["+911", "+915"]

    def test_newest_inbound_wins_not_the_oldest(self, run_both):
        """An old unanswered message followed by a recent answered one is not a
        breach — the clock runs from the NEWEST inbound."""
        sql, py = run_both([
            ("+911", "inbound", 600), ("+911", "inbound", 10),
        ])
        assert sql == py == []

    def test_reply_outside_the_lookback_window_is_not_seen(self, run_both):
        """Both sides bound 'newest outbound' by the same window."""
        sql, py = run_both(
            [("+911", "inbound", 100), ("+911", "outbound", 60 * 50)],
            lookback_hours=48,
        )
        assert sql == py == ["+911"]


# ─────────────────────────────────────────────────────────────────────────────
# Randomised agreement
# ─────────────────────────────────────────────────────────────────────────────

class TestRandomisedAgreement:
    @pytest.mark.parametrize("seed", range(40))
    def test_same_verdict_on_random_traffic(self, run_both, seed):
        rnd = random.Random(seed)
        rows = []
        for phone_n in range(rnd.randint(1, 8)):
            phone = f"+9199000{phone_n:03d}"
            for _ in range(rnd.randint(1, 6)):
                rows.append((
                    phone,
                    rnd.choice(["inbound", "outbound"]),
                    # Straddle the threshold, the tolerance and the window edge.
                    rnd.choice([
                        rnd.uniform(0, 5), rnd.uniform(55, 65),
                        rnd.uniform(100, 400), rnd.uniform(2800, 2920),
                    ]),
                ))
        rnd.shuffle(rows)
        sql, py = run_both(rows)
        assert sql == py, f"seed {seed} diverged\n  sql={sql}\n  py ={py}"


# ─────────────────────────────────────────────────────────────────────────────
# The cap this change removes
# ─────────────────────────────────────────────────────────────────────────────

class TestNoRowCap:
    def test_a_breach_beyond_2000_rows_is_still_found(self, run_both):
        """The bug that motivated this migration.

        The old query took the NEWEST 2000 rows in the window, so once a busy
        48 hours produced more than that, an older unanswered message fell off
        the end and its customer was never alerted. Aggregating in Postgres has
        no cap, so the buried breach is found.
        """
        rows = [("+919999999999", "inbound", 2000)]          # old, unanswered
        rows += [(f"+9190000{i:05d}", "outbound", 1 + i * 0.0001) for i in range(2500)]
        sql, _ = run_both(rows)
        assert "+919999999999" in sql
