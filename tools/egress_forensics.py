#!/usr/bin/env python
"""
Supabase egress forensics — what burned the bandwidth, and when.

Written after the 2026-10-17 outage: the Supabase project hit the free tier's
5 GB/month egress cap and PostgREST started answering every call with

    402 ... restricted due to the following violations: exceed_egress_quota

for fourteen days before anyone noticed. Nothing had been deployed since
2026-09-22, so the cause was traffic, not code. These probes answer "which
traffic" from the data itself.

The cause turned out to be neither storage nor volume. Supabase's own edge
logs put storage GETs at 0-6 per day -- negligible -- while 60-80% of all
requests were one query: an unbounded

    GET /rest/v1/manuscript_transcripts?order=created_at.desc

from website/dtp.html, which carried no `select` and no `limit`, so PostgREST
returned every column of every row -- including `content`, the full OCR
transcript -- and the page re-issued it every 5 seconds. Roughly 15,000
full-table reads over the 12-17 Oct cycle against a 5.5 GB cap works out to
about 380 KB a read, which is what the table actually weighs.

So the probes are ordered payload first: measure what a read of a table costs,
then what pulls it. `transcripts_payload` is the one that quantifies it, and
`table_sizes` finds the next table with the same exposure. The volume probes
further down stay useful for reconciling the outage window, but they are not
where the egress went.

Usage:
    python tools/egress_forensics.py                  # run every probe
    python tools/egress_forensics.py --list           # names + what each shows
    python tools/egress_forensics.py --probe job_volume
    python tools/egress_forensics.py --sql            # print SQL, connect to nothing
    python tools/egress_forensics.py --since 2026-10-01

`--sql` is the one to reach for while the project is still restricted: it needs
no database at all, so you can paste the statements straight into the Supabase
dashboard's SQL editor the moment service is back.

Exit codes:
    0 -- every probe that could run, ran
    1 -- at least one probe failed or was skipped on missing schema
    2 -- configuration error (no DSN, psycopg2 missing, bad arguments)

Requires DATABASE_URL or SUPABASE_DB_URL in the env (the Postgres connection
string from the Supabase dashboard), same as scripts/check_schema.py. Reads
only; it runs no DDL and writes nothing.
"""
from __future__ import annotations

import argparse
import os
import sys
import textwrap
from dataclasses import dataclass, field
from typing import Any

DEFAULT_SINCE = "2026-09-01"
# A paid job still sitting in `Paid` is pullable on every store_puller cycle,
# so it is re-downloaded until it prints or its backoff caps out. Two days is
# well past any legitimate same-day turnaround.
STUCK_AFTER_DAYS = 2


# ─────────────────────────────────────────────────────────────────────────────
# Probe model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Probe:
    """One read-only query, plus what its answer means."""
    name: str
    title: str
    why: str
    sql: str
    # "table.column" (schema defaults to public) or "schema.table.column",
    # or a bare "table" / "schema.table" to require only the relation.
    needs: tuple[str, ...] = field(default=())


PROBES: tuple[Probe, ...] = (

    Probe(
        name="transcripts_payload",
        title="What one read of manuscript_transcripts costs, before and after",
        why="THE decisive probe. `unbounded_read` is what the old dtp.html query "
            "returned on every 5-second poll; `fixed_read` is what the narrowed "
            "query returns now. Multiply unbounded_read by ~15,000 reads per "
            "billing cycle and compare against the 5.5 GB cap -- that is the "
            "outage, in bytes. content_bytes and confidence_bytes show where the "
            "weight sits, and both are now fetched one row at a time on demand.",
        needs=("manuscript_transcripts.content",
               "manuscript_transcripts.confidence_data",
               "manuscript_transcripts.created_at"),
        sql="""
            select
              (select count(*) from manuscript_transcripts) as rows,
              pg_size_pretty((select sum(length(row_to_json(t)::text))
                              from manuscript_transcripts t)::bigint)      as unbounded_read,
              pg_size_pretty((select sum(length(row_to_json(l)::text)) from (
                  select id, filename, mode, status, total_pages,
                         transcribed_pages, uploaded_by_store, created_at
                  from manuscript_transcripts
                  order by created_at desc
                  limit 200) l)::bigint)                                   as fixed_read,
              pg_size_pretty((select sum(length(coalesce(content, '')))
                              from manuscript_transcripts)::bigint)        as content_bytes,
              pg_size_pretty((select sum(length(coalesce(confidence_data::text, '')))
                              from manuscript_transcripts)::bigint)        as confidence_bytes
        """,
    ),

    Probe(
        name="table_sizes",
        title="Every public table by size on disk",
        why="Where else the same mistake would hurt: any table high in this list "
            "that a console reads without a `select` and a `limit` is the next "
            "outage. Cross-check against the sbFetch call sites in website/. "
            "CAVEAT: this is size on DISK, and TOAST compresses long text hard "
            "-- manuscript_transcripts measured 32 kB on disk against a 421 kB "
            "JSON payload. Disk size understates egress, sometimes by 10x. Use "
            "it to rank candidates, then measure the real payload the way "
            "transcripts_payload does.",
        needs=(),
        sql="""
            select relname                                        as table_name,
                   n_live_tup                                     as est_rows,
                   pg_size_pretty(pg_total_relation_size(relid))  as total_size,
                   pg_size_pretty(pg_table_size(relid))           as heap_size,
                   pg_size_pretty(pg_indexes_size(relid))         as index_size
            from pg_catalog.pg_stat_user_tables
            where schemaname = 'public'
            order by pg_total_relation_size(relid) desc
            limit 25
        """,
    ),

    Probe(
        name="wide_column_candidates",
        title="text / json columns, by table size",
        why="A checklist, not a measurement: every text, json or jsonb column "
            "that could be carrying bulk, grouped by how big its table is. "
            "Postgres cannot report per-column wire size without dynamic SQL, "
            "so treat these as the columns to check and to keep out of list "
            "queries. For any that look suspect, measure the payload directly "
            "-- copy the transcripts_payload query and swap the table in.",
        needs=(),
        sql="""
            select c.table_name,
                   c.column_name,
                   c.data_type,
                   pg_size_pretty(pg_total_relation_size(t.relid))  as table_total
            from information_schema.columns c
            join pg_catalog.pg_stat_user_tables t
              on t.schemaname = 'public' and t.relname = c.table_name
            where c.table_schema = 'public'
              and c.data_type in ('text', 'jsonb', 'json')
            order by pg_total_relation_size(t.relid) desc,
                     c.table_name, c.column_name
            limit 40
        """,
    ),

    Probe(
        name="storage_totals",
        title="Storage footprint, per bucket",
        why="Ground truth on what exists to be downloaded. Multiply a bucket's "
            "total by how many times its files get fetched to estimate egress. "
            "`incoming-files` is the public one holding customer print files.",
        needs=("storage.objects.metadata",),
        sql="""
            select bucket_id,
                   count(*)                                                  as files,
                   pg_size_pretty(sum((metadata->>'size')::bigint))           as total,
                   pg_size_pretty(avg((metadata->>'size')::bigint)::bigint)   as avg_file,
                   pg_size_pretty(max((metadata->>'size')::bigint))           as largest
            from storage.objects
            group by bucket_id
            order by sum((metadata->>'size')::bigint) desc nulls last
        """,
    ),

    Probe(
        name="storage_by_day",
        title="Bytes added to storage, per bucket per day",
        why="Upload volume per day. A step change here dates the start of the "
            "spike and names the bucket responsible.",
        needs=("storage.objects.metadata",),
        sql="""
            select created_at::date                                  as day,
                   bucket_id,
                   count(*)                                          as files_added,
                   pg_size_pretty(sum((metadata->>'size')::bigint))  as bytes_added
            from storage.objects
            where created_at >= %(since)s
            group by 1, 2
            order by 1, 2
        """,
    ),

    Probe(
        name="job_volume",
        title="Jobs per day, and the megabytes their files account for",
        why="Print-driven egress, for reconciling the outage window -- not the cause. "
            "Each job's PDF is downloaded from storage at least once per store "
            "PC that prints it, so this column is the floor on it. Measured at 0-6 "
            "storage reads a day during the breach, so this is small; use it to "
            "find jobs that went missing while the API was returning 402.",
        needs=("jobs.received_at", "jobs.file_size_kb"),
        sql="""
            select left(received_at, 10)                            as day,
                   count(*)                                         as jobs,
                   round((sum(coalesce(file_size_kb, 0)) / 1024.0)::numeric, 1) as mb_of_files,
                   round((avg(coalesce(file_size_kb, 0)) / 1024.0)::numeric, 2) as avg_mb
            from jobs
            where received_at >= %(since)s
            group by 1
            order by 1
        """,
    ),

    Probe(
        name="month_compare",
        title="Month over month: jobs and gigabytes of files",
        why="Seasonality test at a glance. Mid-October is university "
            "assignment and project submission season in Thrissur, so an "
            "organic jump is entirely plausible — this says whether it happened.",
        needs=("jobs.received_at", "jobs.file_size_kb"),
        sql="""
            select substr(received_at, 1, 7)                             as month,
                   count(*)                                             as jobs,
                   round((sum(coalesce(file_size_kb, 0)) / 1048576.0)::numeric, 2) as gb_of_files,
                   round((avg(coalesce(file_size_kb, 0)) / 1024.0)::numeric, 2)    as avg_mb
            from jobs
            where received_at >= '2026-06-01'
            group by 1
            order by 1
        """,
    ),

    Probe(
        name="boxes_by_day",
        title="Which box claimed each day's jobs",
        why="Tests the 'a new PC came online' hypothesis. Every store_puller "
            "holds its own Realtime subscription on the whole jobs table and "
            "downloads what it claims, so a box appearing mid-October "
            "multiplies both. A device_id showing up for the first time around "
            "the 10th is your answer.",
        needs=("jobs.received_at", "jobs.print_claimed_by"),
        sql="""
            select left(received_at, 10)                   as day,
                   coalesce(print_claimed_by, '(unclaimed)') as box,
                   count(*)                                 as jobs
            from jobs
            where received_at >= %(since)s
            group by 1, 2
            order by 1, 2
        """,
    ),

    Probe(
        name="stuck_pullable_summary",
        title="Paid-but-never-printed jobs, and the cost of one re-sweep",
        why="A job left in `Paid` stays pullable, so store_puller re-downloads "
            "it every cycle until it prints or its backoff caps. mb_per_resweep "
            "is what one full sweep of these costs; multiply by sweeps per day "
            "(and by boxes) for the real bill. A handful of unprintable PDFs "
            "can quietly dominate a month.",
        needs=("jobs.status", "jobs.received_at", "jobs.file_size_kb"),
        sql="""
            select count(*)                                          as stuck_jobs,
                   round((sum(coalesce(file_size_kb, 0)) / 1024.0)::numeric, 1) as mb_per_resweep
            from jobs
            where status = 'Paid'
              and received_at < %(stuck_before)s
        """,
    ),

    Probe(
        name="stuck_pullable_list",
        title="The stuck jobs themselves",
        why="The individual offenders behind the summary above, oldest first. "
            "A large file here that has sat for weeks is the single most likely "
            "cause of a sudden, code-free egress spike.",
        needs=("jobs.status", "jobs.received_at", "jobs.file_size_kb"),
        sql="""
            select job_id,
                   store_id,
                   assigned_store_id,
                   received_at,
                   file_size_kb,
                   print_claimed_at,
                   filename
            from jobs
            where status = 'Paid'
              and received_at < %(stuck_before)s
            order by received_at
            limit 50
        """,
    ),

    Probe(
        name="retry_amplification",
        title="Jobs with an unusual number of events",
        why="A proxy for retry churn: repeated print attempts on one job mean "
            "repeated downloads of the same file. Cross-reference file_size_kb "
            "to turn the event count into megabytes.",
        needs=("job_events.job_id", "job_events.created_at"),
        sql="""
            select e.job_id,
                   count(*)                 as events,
                   min(e.created_at)        as first_event,
                   max(e.created_at)        as last_event,
                   max(j.file_size_kb)      as file_size_kb,
                   max(j.status)            as status
            from job_events e
            left join jobs j on j.job_id = e.job_id
            where e.created_at >= %(since)s
            group by e.job_id
            having count(*) > 5
            order by count(*) desc
            limit 30
        """,
    ),

    Probe(
        name="largest_files",
        title="Largest files in the window",
        why="One 200 MB scan re-pulled a few times outweighs a thousand "
            "ordinary jobs. Check these against retry_amplification and "
            "stuck_pullable_list — an overlap is the smoking gun.",
        needs=("jobs.received_at", "jobs.file_size_kb"),
        sql="""
            select job_id,
                   filename,
                   file_size_kb,
                   page_count,
                   status,
                   received_at
            from jobs
            where received_at >= %(since)s
              and file_size_kb is not null
            order by file_size_kb desc
            limit 25
        """,
    ),

    Probe(
        name="conversation_media",
        title="Media messages logged per day",
        why="Every inbound media message is stored and may be re-fetched by "
            "the admin console. Rising counts point at inbound volume — ad "
            "spend or a campaign — rather than at printing.",
        needs=("conversation_log.created_at", "conversation_log.media_url"),
        sql="""
            select created_at::date as day,
                   message_type,
                   count(*)         as media_rows
            from conversation_log
            where created_at >= %(since)s
              and media_url is not null
            group by 1, 2
            order by 1, 2
        """,
    ),

    Probe(
        name="academic_outputs",
        title="Academic orders per day",
        why="Academic work writes large DOCX/PDF renders to storage and is the "
            "most seasonal part of the business. October is exactly when this "
            "spikes on its own.",
        needs=("academic_orders.created_at",),
        sql="""
            select created_at::date as day,
                   status,
                   count(*)         as orders
            from academic_orders
            where created_at >= %(since)s
            group by 1, 2
            order by 1, 2
        """,
    ),

    Probe(
        name="project_builder_outputs",
        title="Project-builder orders per day",
        why="The other render-heavy pipeline writing generated documents to "
            "the public bucket.",
        needs=("project_builder_orders.created_at",),
        sql="""
            select created_at::date as day,
                   count(*)         as orders
            from project_builder_orders
            where created_at >= %(since)s
            group by 1
            order by 1
        """,
    ),

    Probe(
        name="active_leases",
        title="Current role leases",
        why="Which box currently holds each shared role. More owner_devices "
            "than you have store PCs means a stale or duplicated box is still "
            "polling and still paying for egress.",
        needs=("store_role_leases.owner_device",),
        sql="""
            select store_id,
                   role,
                   owner_device,
                   acquired_at,
                   expires_at,
                   expires_at < now() as expired
            from store_role_leases
            order by store_id, role
        """,
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Schema introspection — skip loudly, never crash mid-run
# ─────────────────────────────────────────────────────────────────────────────

def _split_need(need: str) -> tuple[str, str, str | None]:
    """'jobs.file_size_kb' -> ('public','jobs','file_size_kb'); 'storage.objects' -> ('storage','objects',None)."""
    parts = need.split(".")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        # Ambiguous by shape: a leading schema name means schema.table,
        # anything else means public.table.column. "public" belongs in that
        # first set -- leaving it out read "public.jobs" as public.public.jobs.
        if parts[0] in ("public", "storage", "auth", "realtime", "extensions"):
            return parts[0], parts[1], None
        return "public", parts[0], parts[1]
    return "public", parts[0], None


def fetch_available_columns(cur) -> set[tuple[str, str, str]]:
    """Every (schema, table, column) we might care about, in one round trip."""
    cur.execute(
        """
        select table_schema, table_name, column_name
        from information_schema.columns
        where table_schema in ('public', 'storage')
        """
    )
    return {(r["table_schema"], r["table_name"], r["column_name"]) for r in cur.fetchall()}


def missing_requirements(probe: Probe, available: set[tuple[str, str, str]]) -> list[str]:
    """Which of a probe's needs the live schema cannot satisfy."""
    tables = {(s, t) for (s, t, _) in available}
    gaps: list[str] = []
    for need in probe.needs:
        schema, table, column = _split_need(need)
        if column is None:
            if (schema, table) not in tables:
                gaps.append(f"{schema}.{table} (relation)")
        elif (schema, table, column) not in available:
            gaps.append(f"{schema}.{table}.{column}")
    return gaps


# ─────────────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────────────

def render_rows(rows: list[dict[str, Any]]) -> str:
    """Column-aligned text table. Empty result sets say so rather than printing nothing."""
    if not rows:
        return "    (no rows)"
    headers = list(rows[0].keys())
    widths = {h: len(h) for h in headers}
    cells: list[list[str]] = []
    for row in rows:
        line = []
        for h in headers:
            text = "" if row[h] is None else str(row[h])
            widths[h] = max(widths[h], len(text))
            line.append(text)
        cells.append(line)
    out = ["    " + "  ".join(h.ljust(widths[h]) for h in headers),
           "    " + "  ".join("-" * widths[h] for h in headers)]
    for line in cells:
        out.append("    " + "  ".join(
            text.ljust(widths[h]) for h, text in zip(headers, line)))
    return "\n".join(out)


def inline_params(sql: str, params: dict[str, str]) -> str:
    """Substitute the named params as SQL literals, for copy-paste into the dashboard.

    Dedented, because the whole point of --sql is pasting it somewhere else.
    """
    text = textwrap.dedent(sql).strip()
    for key, value in params.items():
        text = text.replace(f"%({key})s", f"'{value}'")
    return text


def print_header(probe: Probe) -> None:
    print(f"\n=== {probe.title}  [{probe.name}]")
    print(f"    {probe.why}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def build_params(since: str) -> dict[str, str]:
    """`jobs.received_at` is TEXT ISO-8601 (legacy from SQLite), so its bounds
    are compared as strings — correct for ISO-8601, which sorts lexically."""
    from datetime import date, timedelta
    stuck_before = (date.today() - timedelta(days=STUCK_AFTER_DAYS)).isoformat()
    return {"since": since, "stuck_before": stuck_before}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Find what burned the Supabase egress budget, and when.")
    ap.add_argument("--since", default=DEFAULT_SINCE,
                    help=f"ISO date lower bound for the windowed probes (default {DEFAULT_SINCE})")
    ap.add_argument("--probe", action="append", metavar="NAME",
                    help="run only this probe; repeatable")
    ap.add_argument("--list", action="store_true",
                    help="list probe names and what each one shows, then exit")
    ap.add_argument("--sql", action="store_true",
                    help="print the SQL with literals inlined and exit; connects to nothing")
    args = ap.parse_args(argv)

    selected = PROBES
    if args.probe:
        wanted = set(args.probe)
        unknown = wanted - {p.name for p in PROBES}
        if unknown:
            sys.stderr.write(
                f"ERROR: unknown probe(s): {', '.join(sorted(unknown))}\n"
                f"Run --list to see the available names.\n")
            return 2
        selected = tuple(p for p in PROBES if p.name in wanted)

    if args.list:
        for probe in PROBES:
            print(f"{probe.name}\n    {probe.title}\n    {probe.why}\n")
        return 0

    params = build_params(args.since)

    if args.sql:
        print("-- Supabase egress forensics. Read-only; paste into the SQL editor.")
        print(f"-- Window: {params['since']} onward. "
              f"Stuck-job cutoff: {params['stuck_before']}.")
        for probe in selected:
            print(f"\n-- ── {probe.title}  [{probe.name}]")
            print(f"-- {probe.why}")
            print(inline_params(probe.sql, params) + ";")
        return 0

    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        sys.stderr.write(
            "ERROR: DATABASE_URL (or SUPABASE_DB_URL) is required. Get the Postgres\n"
            "connection string from the Supabase dashboard (Project Settings →\n"
            "Database → Connection string → URI).\n\n"
            "To read the queries without a database, run with --sql.\n")
        return 2

    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        sys.stderr.write("ERROR: psycopg2-binary not installed. Run:\n"
                         "  pip install psycopg2-binary\n")
        return 2

    print(f"Window: {params['since']} onward.  "
          f"Stuck-job cutoff: {params['stuck_before']}.")

    conn = psycopg2.connect(dsn)
    problems = 0
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        available = fetch_available_columns(cur)

        for probe in selected:
            gaps = missing_requirements(probe, available)
            if gaps:
                # Loud skip: a probe silently absent from the output would make
                # a missing cause look like an absent one.
                print_header(probe)
                print(f"    SKIPPED — schema does not provide: {', '.join(gaps)}")
                problems += 1
                continue
            print_header(probe)
            try:
                cur.execute(probe.sql, params)
                print(render_rows([dict(r) for r in cur.fetchall()]))
            except Exception as exc:          # noqa: BLE001 - reported, never swallowed
                conn.rollback()
                print(f"    FAILED — {type(exc).__name__}: {exc}")
                problems += 1
    finally:
        conn.close()

    if problems:
        print(f"\n{problems} probe(s) failed or were skipped — see above.")
        return 1
    print("\nAll probes completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
