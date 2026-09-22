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

def _migration(name):
    return os.path.join(os.path.dirname(__file__), "..", "api", "migrations", name)


# v45 replaces the function with the same signature plus a channel filter, so
# both are applied in order — what CI runs against is what production runs.
_MIGRATIONS = [_migration("SCHEMA_v44_sla_breaches_rpc.sql"),
               _migration("SCHEMA_v45_instagram_dm.sql")]

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
    channel       TEXT NOT NULL DEFAULT 'whatsapp',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- v45 also touches ad_clicks and creates instagram_threads on its way to
-- replacing sla_breaches(). Stubbed to the columns it alters so the migration
-- applies here exactly as it does in production, rather than being excerpted
-- — an excerpt is a second copy of the SQL that can drift from the real one.
DROP TABLE IF EXISTS public.ad_clicks CASCADE;
CREATE TABLE public.ad_clicks (
    id          BIGSERIAL PRIMARY KEY,
    phone       TEXT NOT NULL,
    channel     TEXT NOT NULL DEFAULT 'whatsapp',
    wamid       TEXT UNIQUE,
    source_id   TEXT,
    clicked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
DROP TABLE IF EXISTS public.instagram_threads CASCADE;
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
    for path in _MIGRATIONS:
        with open(path, encoding="utf-8") as fh:
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


class TestTheSweepStaysAWhatsAppSweep:
    """v45 adds `AND cl.channel = 'whatsapp'`.

    Instagram rows put an IGSID in conversation_log.phone. Without the filter
    the sweep would report a "customer" nobody can dial, every 30 minutes
    forever: the cooldown that stops it repeating lives in whatsapp_contacts,
    and an IGSID never matches a row there. Instagram is watched instead by
    instagram_threads.needs_human and the `instagram.queue` check.
    """

    def _breaches(self, dsn):
        out = _psql(
            "SELECT phone FROM public.sla_breaches(1, 120, 48);",
            dsn, want_rows=True,
        )
        return sorted(l.strip() for l in out.splitlines() if l.strip())

    def test_an_unanswered_instagram_dm_is_not_an_sla_breach(self, dsn):
        _psql("TRUNCATE public.conversation_log;", dsn)
        _psql(
            "INSERT INTO public.conversation_log (phone, direction, channel, created_at) "
            "VALUES ('17841400000000123','inbound','instagram', now() - interval '5 hours');",
            dsn,
        )
        assert self._breaches(dsn) == []

    def test_an_unanswered_whatsapp_message_still_is(self, dsn):
        _psql("TRUNCATE public.conversation_log;", dsn)
        _psql(
            "INSERT INTO public.conversation_log (phone, direction, created_at) "
            "VALUES ('919495706405','inbound', now() - interval '5 hours');",
            dsn,
        )
        assert self._breaches(dsn) == ["919495706405"]

    def test_an_instagram_reply_cannot_clear_a_whatsapp_breach(self, dsn):
        """Same string in `phone` on two channels must not cancel out — that
        would silence a real customer because of an unrelated DM."""
        _psql("TRUNCATE public.conversation_log;", dsn)
        _psql(
            "INSERT INTO public.conversation_log (phone, direction, channel, created_at) VALUES "
            "('919495706405','inbound','whatsapp',  now() - interval '5 hours'),"
            "('919495706405','outbound','instagram', now() - interval '1 minute');",
            dsn,
        )
        assert self._breaches(dsn) == ["919495706405"]

    def test_existing_rows_keep_their_meaning(self, dsn):
        """The column defaults to whatsapp, so every row written before v45
        must still be swept."""
        _psql("TRUNCATE public.conversation_log;", dsn)
        _psql(
            "INSERT INTO public.conversation_log (phone, direction, created_at) "
            "VALUES ('918907318168','inbound', now() - interval '3 hours');",
            dsn,
        )
        rows = _psql("SELECT channel FROM public.conversation_log;", dsn, want_rows=True)
        assert rows.strip() == "whatsapp"
        assert self._breaches(dsn) == ["918907318168"]
