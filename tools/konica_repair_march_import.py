#!/usr/bin/env python3
"""Repair the 2026-03-16 Konica CSV import. Dry-run by default.

    python tools/konica_repair_march_import.py              # show, change nothing
    python tools/konica_repair_march_import.py --apply      # write it

WHAT WENT WRONG
---------------
One CSV import on 2026-03-16 wrote 5,000 rows, of which 528 have no `job_date`
and so are invisible to every dated report and never reach the cloud (the sync
takes the newest 2,000 by job_date, and NULL sorts last). Two different faults,
found 2026-09-03:

  479 rows  the row is SOUND — filename, result and page counts all sensible —
            and only the date failed to parse. 22,985 pages.
   49 rows  the FIELDS SHIFTED. The CSV did not quote filenames, so a comma
            inside one split the row and pushed every later field left:

                file_name = 'Scromboid fish poisoning'
                result    = 'Trichinosis'

            — one filename, "Scromboid fish poisoning, Trichinosis", torn in
            half. 1,164 pages.

The live SOAP fetcher produced none of this: all 13,062 of its rows are clean,
and konica_csv_importer has not run since April. This is wreckage from a retired
path, so it is repaired once, here, rather than guarded against at ingest.

WHAT THIS DOES
--------------
Recovers the 479, quarantines the 49, and never guesses:

  print_end   the row kept a print_end_date. It is a recorded time, but a
              recorded time is not automatically the right DAY — most of the
              surviving ones sit within minutes of 13:00, which looks like a
              scheduled export rather than a print finishing. So print_end is
              never taken on trust: it is CHECKED against the bracket below,
              and used only where the two agree (or where the row has no dated
              neighbour at all to check it against, which is then said out
              loud). Where they disagree the row is LEFT ALONE.
  bracketed   job_number is monotonic with time, and these rows sit among
              thousands of correctly dated ones from the same import, so an
              undated row is BRACKETED between its dated neighbours. When both
              neighbours fall on the same day, that day is certain and the row
              takes the midpoint. When they straddle midnight the true day is
              one of two and picking is a coin flip, so the row is LEFT ALONE —
              unless a print_end lands inside that range and settles it.
  quarantined the 49 shifted rows. Their columns cannot be trusted, and
              un-shifting needs the comma count per row, which is not
              recoverable. They keep their NULL job_date, so they stay out of
              every count and out of the cloud — but `date_source` now says WHY,
              instead of leaving the next person to work it out again.

A wrong date is worse than no date. That is the rule store_digest already
follows for a finishing transfer with no send time, and it is why the two
methods must corroborate rather than merely both being available.

Every touched row is stamped in `date_source`, so an inferred date can never be
mistaken for a recorded one.

AFTER APPLYING: recovered rows stop being NULL, enter the sync window, and start
appearing in cloud reports. March figures will move. That is the point, but it
is not silent.
"""
import argparse
import bisect
import os
import sqlite3
import sys
from datetime import datetime

DEFAULT_DB = os.environ.get("PRINTOSKY_DB", r"C:\Printosky\Data\jobs.db")

#: Results the CSV importer wrote. A row whose `result` is none of these has had
#: a filename fragment pushed into the column.
KNOWN = ("No Error", "Canceled", "Error")

RECORDED    = "recorded"
PRINT_END   = "print_end"
BRACKETED   = "bracketed"
QUARANTINED = "quarantined:fields-shifted"

#: Why a print_end date was accepted — recorded on the row so a later reader can
#: tell a corroborated date from an unchecked one.
CORROBORATED   = "agrees with the dated neighbours"
SETTLES_RANGE  = "lands inside the neighbours' range and settles it"
UNCORROBORATED = "NOT CHECKED — no dated neighbour to check it against"


def _day(ts):
    return str(ts)[:10] if ts else None


def _parse(ts):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(ts).strip()[:19], fmt)
        except (ValueError, TypeError):
            continue
    return None


def print_end_agreement(conn):
    """How often print_end_date's DAY matches job_date's, where both survive.

    The repair leans on print_end for a handful of rows; this says, from the
    thousands of rows that kept both, whether that column means what it looks
    like it means. It is a report, not a gate — the per-row corroboration in
    plan() is what actually decides.
    """
    agree = total = 0
    for job_date, print_end in conn.execute(
            "SELECT job_date, print_end_date FROM konica_jobs "
            "WHERE job_date IS NOT NULL AND print_end_date IS NOT NULL"):
        if not _parse(print_end):
            continue
        total += 1
        agree += _day(job_date) == _day(print_end)
    return {"agree": agree, "total": total}


def _bracket(number, numbers, by_number):
    """The dated rows either side of `number`.

    Returns (before, after, refusal). `refusal` is set when there is nothing to
    bracket against, in which case before/after are None.
    """
    if number is None:
        return None, None, "no job_number"
    i = bisect.bisect_left(numbers, number)
    before = by_number[numbers[i - 1]] if i > 0 else None
    after = by_number[numbers[i]] if i < len(numbers) else None
    if not before or not after:
        return None, None, "no neighbour on one side"
    if not _parse(before) or not _parse(after):
        return None, None, "unparseable neighbour"
    return before, after, None


def plan(conn, use_print_end=True):
    """Work out every change without making one."""
    dated = conn.execute(
        "SELECT job_number, job_date FROM konica_jobs "
        "WHERE job_date IS NOT NULL AND job_number IS NOT NULL "
        "ORDER BY job_number").fetchall()
    undated = conn.execute(
        "SELECT job_number, result, print_end_date, COALESCE(pages_printed,0) "
        "FROM konica_jobs WHERE job_date IS NULL ORDER BY job_number").fetchall()

    numbers = [n for n, _ in dated]
    by_number = dict(dated)

    actions = {PRINT_END: [], BRACKETED: [], QUARANTINED: [],
               "conflict": [], "ambiguous": [], "unbracketable": []}

    for number, result, print_end, pages in undated:
        if result not in KNOWN:
            actions[QUARANTINED].append((number, None, pages, result))
            continue

        recorded = _parse(print_end) if use_print_end else None
        before, after, refusal = _bracket(number, numbers, by_number)

        if refusal:
            # Nothing to check against and nothing to bracket with.
            if recorded:
                actions[PRINT_END].append(
                    (number, str(print_end)[:19], pages, UNCORROBORATED))
            else:
                actions["unbracketable"].append((number, None, pages, refusal))
            continue

        lo_day, hi_day = _day(before), _day(after)
        certain = lo_day == hi_day

        if recorded:
            recorded_day = recorded.strftime("%Y-%m-%d")
            if lo_day <= recorded_day <= hi_day:
                note = CORROBORATED if certain else SETTLES_RANGE
                actions[PRINT_END].append(
                    (number, str(print_end)[:19], pages, note))
            else:
                # Two sources, two answers. Neither is worth writing.
                actions["conflict"].append(
                    (number, None, pages,
                     f"print_end says {recorded_day}, neighbours say "
                     + (lo_day if certain else f"{lo_day} .. {hi_day}")))
            continue

        if not certain:
            actions["ambiguous"].append(
                (number, None, pages, f"{lo_day} .. {hi_day}"))
            continue

        lo, hi = _parse(before), _parse(after)
        mid = lo + (hi - lo) / 2
        actions[BRACKETED].append((number, mid.strftime("%Y-%m-%d %H:%M:%S"), pages, None))

    return actions


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"jobs.db (default: {DEFAULT_DB})")
    ap.add_argument("--apply", action="store_true",
                    help="write the changes. Without this, nothing is modified.")
    ap.add_argument("--no-print-end", action="store_true",
                    help="ignore print_end_date entirely, so every recovered "
                         "date comes from bracketing and carries one caveat.")
    ap.add_argument("--show", type=int, default=5, metavar="N",
                    help="example rows to print per group (default 5)")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"database not found: {args.db}\n  Pass --db, or set PRINTOSKY_DB.")

    conn = sqlite3.connect(args.db)
    try:
        actions = plan(conn, use_print_end=not args.no_print_end)
        agreement = print_end_agreement(conn)
    except sqlite3.OperationalError as exc:
        sys.exit(f"could not read konica_jobs: {exc}")

    print(f"database : {args.db}")
    print(f"mode     : {'APPLY — this will write' if args.apply else 'DRY RUN — nothing is changed'}")
    if args.no_print_end:
        print("print_end: IGNORED (--no-print-end) — every date below is bracketed")
    elif agreement["total"]:
        pct = 100.0 * agreement["agree"] / agreement["total"]
        print(f"print_end: agrees with job_date on {agreement['agree']:,} of "
              f"{agreement['total']:,} rows that kept both ({pct:.1f}%)")
    else:
        print("print_end: no dated row kept one, so the column cannot be "
              "checked here — only per-row corroboration below applies")
    print()

    order = [PRINT_END, BRACKETED, QUARANTINED, "conflict", "ambiguous", "unbracketable"]
    label = {
        PRINT_END:     "dated from print_end_date (recorded, checked below)",
        BRACKETED:     "dated by bracketing between dated neighbours (inferred)",
        QUARANTINED:   "quarantined — fields shifted, columns untrustworthy",
        "conflict":    "LEFT ALONE — print_end and the neighbours disagree",
        "ambiguous":   "LEFT ALONE — neighbours straddle midnight, day is a coin flip",
        "unbracketable": "LEFT ALONE — no neighbour to bracket against",
    }
    total_pages = 0
    for key in order:
        rows = actions[key]
        pages = sum(r[2] for r in rows)
        if key in (PRINT_END, BRACKETED):
            total_pages += pages
        print(f"{len(rows):>5} rows  {pages:>7} pages   {label[key]}")
        for number, new_date, page_n, note in rows[:args.show]:
            extra = f"  ({note})" if note else ""
            print(f"           job {number}  ->  {new_date or 'unchanged'}{extra}")
        if len(rows) > args.show:
            print(f"           … and {len(rows) - args.show} more")
    print(f"\n{total_pages} pages would return to the dated reports.")

    unchecked = [r for r in actions[PRINT_END] if r[3] == UNCORROBORATED]
    if unchecked:
        print(f"{len(unchecked)} of the print_end rows had no dated neighbour to "
              f"check against. Re-run with --no-print-end to leave them alone.")

    if not args.apply:
        print("\nNothing was changed. Re-run with --apply to write it.")
        return

    # `date_source` makes an inferred date impossible to mistake for a recorded
    # one. Added here rather than in db_migrations: this is a one-off repair
    # column, not part of the running schema.
    have = {r[1] for r in conn.execute("PRAGMA table_info(konica_jobs)")}
    if "date_source" not in have:
        conn.execute("ALTER TABLE konica_jobs ADD COLUMN date_source TEXT")

    written = 0
    for key in (PRINT_END, BRACKETED):
        conn.executemany(
            "UPDATE konica_jobs SET job_date = ?, date_source = ? WHERE job_number = ?",
            [(new_date, key, number) for number, new_date, _, _ in actions[key]])
        written += len(actions[key])
    conn.executemany(
        "UPDATE konica_jobs SET date_source = ? WHERE job_number = ?",
        [(QUARANTINED, number) for number, _, _, _ in actions[QUARANTINED]])
    conn.commit()

    left = conn.execute(
        "SELECT COUNT(1) FROM konica_jobs WHERE job_date IS NULL").fetchone()[0]
    print(f"\nWritten. {written} rows dated, "
          f"{len(actions[QUARANTINED])} quarantined, {left} rows still undated.")
    if written:
        print("Recovered rows will sync to the cloud on the next cycle; March "
              "figures will change.")
    else:
        print("Nothing needed dating — already repaired.")


if __name__ == "__main__":
    main()
