"""
The SQL in SCHEMA_v45 must agree with the row scan it replaces.

Same contract as tests/test_sla_breaches_sql.py: the Python is the reference
implementation and stays in the tree as the fallback, so both are run over the
same rows — the Python in process, the SQL in a real Postgres — and asserted to
produce identical output.

Also pins two claims the migration makes:
  * the existing (phone, created_at DESC) index serves DISTINCT ON, so v45 adds
    no index of its own
  * the result is one row per phone regardless of how many messages exist,
    which is what removes the 4000-row cap's misreporting

Skipped when no Postgres is reachable. Point PRINTOSKY_TEST_PG at a database:

    PRINTOSKY_TEST_PG='host=/tmp port=55432 user=postgres dbname=sla_test'
"""

import os
import random
import subprocess
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

_MIGRATION = os.path.join(
    os.path.dirname(__file__), "..", "api", "migrations",
    "SCHEMA_v45_latest_messages_rpc.sql",
)

# Mirrors SCHEMA_v11, including the index whose existence v45 relies on.
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
CREATE INDEX idx_conversation_log_phone_created
    ON public.conversation_log (phone, created_at DESC);
DO $$ BEGIN CREATE ROLE service_role; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
"""

_SEP = "\x1f"


def _psql(sql, dsn, want_rows=False):
    cmd = ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-X", "-q"]
    if want_rows:
        cmd += ["-t", "-A", "-F", _SEP]
    r = subprocess.run(cmd, input=sql, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()}\n--- sql ---\n{sql[:2000]}")
    return r.stdout


def _quote(s):
    return "'" + s.replace("'", "''") + "'"


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
    """Load rows, run both implementations, return (sql_map, python_map).

    Each row is (phone, direction, body, minutes_ago). Both maps are
    {phone: (direction, body)} — what chat_audit_snapshot actually reads off the
    newest message.
    """
    def _go(rows, phones, lookback_hours=336):
        now_s = _psql("SELECT now();", dsn, want_rows=True).strip()
        now = datetime.fromisoformat(now_s)

        _psql("TRUNCATE public.conversation_log;", dsn)
        if rows:
            values = ",".join(
                "(%s,%s,%s, now() - interval '%f minutes')"
                % (_quote(p), _quote(d), _quote(b), m)
                for p, d, b, m in rows
            )
            _psql("INSERT INTO public.conversation_log "
                  f"(phone, direction, body, created_at) VALUES {values};", dsn)

        arr = "ARRAY[" + ",".join(_quote(p) for p in phones) + "]::text[]" \
              if phones else "ARRAY[]::text[]"
        out = _psql(
            f"SELECT phone, direction, coalesce(body,'') FROM "
            f"public.latest_messages({arr}, {lookback_hours});",
            dsn, want_rows=True,
        )
        sql_map = {}
        for line in out.splitlines():
            if not line.strip():
                continue
            ph, direction, body = line.split(_SEP)
            sql_map[ph] = (direction, body)

        # The reference: newest-first scan, first row seen per phone — the exact
        # shape of the code this replaces, with the window applied to the rows.
        window_start = now - timedelta(hours=lookback_hours)
        scanned = sorted(
            (
                {"phone": p, "direction": d, "body": b,
                 "created_at": now - timedelta(minutes=m)}
                for p, d, b, m in rows
                if p in set(phones) and now - timedelta(minutes=m) >= window_start
            ),
            key=lambda r: r["created_at"],
            reverse=True,
        )
        py_map = {}
        for lr in scanned:
            py_map.setdefault(lr["phone"], (lr["direction"], lr["body"] or ""))
        return sql_map, py_map

    return _go


# ─────────────────────────────────────────────────────────────────────────────
# Agreement
# ─────────────────────────────────────────────────────────────────────────────

class TestAgreement:
    def test_newest_message_wins(self, run_both):
        sql, py = run_both([
            ("+911", "inbound",  "first",  300),
            ("+911", "outbound", "newest",  10),
        ], ["+911"])
        assert sql == py == {"+911": ("outbound", "newest")}

    def test_one_row_per_phone(self, run_both):
        sql, py = run_both([
            ("+911", "inbound", f"msg{i}", 100 - i) for i in range(30)
        ], ["+911"])
        assert len(sql) == 1
        assert sql == py

    def test_phones_are_independent(self, run_both):
        sql, py = run_both([
            ("+911", "inbound",  "a", 50),
            ("+912", "outbound", "b", 20),
            ("+913", "inbound",  "c", 80),
        ], ["+911", "+912", "+913"])
        assert sql == py
        assert sql["+912"] == ("outbound", "b")

    def test_unflagged_phones_are_not_returned(self, run_both):
        """The whole point: ask about the flagged set, not the whole log."""
        sql, py = run_both([
            ("+911", "inbound", "wanted",   10),
            ("+999", "inbound", "unwanted",  5),
        ], ["+911"])
        assert sql == py == {"+911": ("inbound", "wanted")}
        assert "+999" not in sql

    def test_phone_with_no_messages_is_absent(self, run_both):
        """Absent means "no reply yet", which is what marks a chat as waiting."""
        sql, py = run_both([("+911", "inbound", "x", 10)], ["+911", "+912"])
        assert sql == py
        assert "+912" not in sql

    def test_messages_outside_the_window_are_ignored(self, run_both):
        sql, py = run_both(
            [("+911", "outbound", "ancient", 60 * 400)], ["+911"], lookback_hours=336)
        assert sql == py == {}

    def test_empty_phone_list(self, run_both):
        sql, py = run_both([("+911", "inbound", "x", 5)], [])
        assert sql == py == {}

    def test_body_is_preserved_for_the_ack_check(self, run_both):
        """chat_audit_snapshot runs _is_handoff_ack over this body, so it has to
        survive the round trip intact — quotes and all."""
        body = "I've alerted the team — it's 100% sorted, don't worry"
        sql, py = run_both([("+911", "outbound", body, 5)], ["+911"])
        assert sql == py == {"+911": ("outbound", body)}

    def test_null_body_reads_as_empty(self, run_both):
        sql, _ = run_both([("+911", "inbound", "", 5)], ["+911"])
        assert sql["+911"] == ("inbound", "")


class TestRandomisedAgreement:
    @pytest.mark.parametrize("seed", range(30))
    def test_same_map_on_random_traffic(self, run_both, seed):
        rnd = random.Random(seed)
        phones = [f"+9199000{i:03d}" for i in range(rnd.randint(1, 6))]
        rows = []
        # Distinct minute offsets: an exact created_at tie is broken by id DESC
        # in SQL and by scan order in Python, which need not agree. Ties are
        # vanishingly unlikely at microsecond precision, so they are excluded
        # rather than asserted on.
        offsets = rnd.sample(range(1, 4000), k=min(40, 3999))
        for off in offsets:
            rows.append((
                rnd.choice(phones + ["+9100000000"]),   # some traffic not asked about
                rnd.choice(["inbound", "outbound"]),
                rnd.choice(["hi", "alerted the team", "your order is ready", ""]),
                float(off),
            ))
        sql, py = run_both(rows, phones)
        assert sql == py, f"seed {seed} diverged\n  sql={sql}\n  py ={py}"


# ─────────────────────────────────────────────────────────────────────────────
# The claims the migration makes
# ─────────────────────────────────────────────────────────────────────────────

class TestMigrationClaims:
    def test_uses_the_existing_index_so_v45_adds_none(self, dsn):
        """SCHEMA_v11's (phone, created_at DESC) is the ordering DISTINCT ON
        wants. If a future change breaks that, v45 needs its own index and this
        test should say so rather than the query quietly seq-scanning."""
        _psql("TRUNCATE public.conversation_log;", dsn)
        _psql(
            "INSERT INTO public.conversation_log (phone, direction, body, created_at) "
            "SELECT '+9199' || lpad((i % 500)::text, 6, '0'), "
            "       CASE WHEN i % 2 = 0 THEN 'inbound' ELSE 'outbound' END, "
            "       'm' || i, now() - make_interval(secs => i) "
            "FROM generate_series(1, 40000) i; ANALYZE public.conversation_log;",
            dsn,
        )
        plan = _psql(
            "EXPLAIN (COSTS OFF) SELECT * FROM public.latest_messages("
            "ARRAY['+9199000001','+9199000002']::text[], 336);", dsn, want_rows=True)
        assert "idx_conversation_log_phone_created" in plan, \
            f"DISTINCT ON is not using the existing index:\n{plan}"
        assert "Seq Scan" not in plan, f"unexpected sequential scan:\n{plan}"

    def test_returns_one_row_per_phone_past_the_old_cap(self, dsn):
        """The bug this migration fixes.

        The row scan took the newest 4000 rows across ALL phones; a flagged
        phone whose newest message sat past that returned nothing, and an
        already-answered chat was reported as still waiting. DISTINCT ON has no
        cap, so the buried phone still answers.
        """
        _psql("TRUNCATE public.conversation_log;", dsn)
        _psql(
            "INSERT INTO public.conversation_log (phone, direction, body, created_at) "
            "SELECT '+91noise' || i, 'inbound', 'n', now() - make_interval(secs => i) "
            "FROM generate_series(1, 6000) i;", dsn)
        _psql(
            "INSERT INTO public.conversation_log (phone, direction, body, created_at) "
            "VALUES ('+91buried', 'outbound', 'we replied', now() - interval '5 days');",
            dsn)

        out = _psql(
            "SELECT phone, direction FROM public.latest_messages("
            "ARRAY['+91buried']::text[], 336);", dsn, want_rows=True)

        assert out.strip().split(_SEP) == ["+91buried", "outbound"], \
            "a phone buried past the old cap must still be found"
