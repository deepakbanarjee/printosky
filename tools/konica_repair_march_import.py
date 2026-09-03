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

  print_end   the row kept a print_end_date. Use it. Nothing is inferred.
  bracketed   job_number is monotonic with time, and these rows sit among
              thousands of correctly dated ones from the same import, so an
              undated row is BRACKETED between its dated neighbours. When both
              neighbours fall on the same day, that day is certain and the row
              takes the midpoint. When they straddle midnight the true day is
              one of two and picking is a coin flip, so the row is LEFT ALONE —
              a wrong date is worse than no date, which is the rule
              store_digest already follows for a finishing transfer with no
              send time.
  quarantined the 49 shifted rows. Their columns cannot be trusted, and
              un-shifting needs the comma count per row, which is not
              recoverable. They keep their NULL job_date, so they stay out of
              every count and out of the cloud — but `date_source` now says WHY,
              instead of leaving the next person to work it out again.

Every touched row is stamped in `date_source`, so an inferred date can never be
mistaken for a recorded one.

AFTER APPLYING: recovered rows stop being NULL, enter the sync window, and start
appearing in cloud reports. March figures will move. That is the point, but it
is not silent.
"""
import argparse
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


def _day(ts):
    return str(ts)[:10] if ts else None


def _parse(ts):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(ts).strip()[:19], fmt)
        except (ValueError, TypeError):
            continue
    return None


def plan(conn):
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

    import bisect
    actions = {PRINT_END: [], BRACKETED: [], QUARANTINED: [],
               "ambiguous": [], "unbracketable": []}

    for number, result, print_end, pages in undated:
        if result not in KNOWN:
            actions[QUARANTINED].append((number, None, pages, result))
            continue
        if _parse(print_end):
            actions[PRINT_END].append((number, str(print_end)[:19], pages, None))
            continue
        if number is None:
            actions["unbracketable"].append((number, None, pages, "no job_number"))
            continue

        i = bisect.bisect_left(numbers, number)
        before = by_number[numbers[i - 1]] if i > 0 else None
        after = by_number[numbers[i]] if i < len(numbers) else None
        if not before or not after:
            actions["unbracketable"].append(
                (number, None, pages, "no neighbour on one side"))
            continue
        if _day(before) != _day(after):
            actions["ambiguous"].append(
                (number, None, pages, f"{_day(before)} .. {_day(after)}"))
            continue

        lo, hi = _parse(before), _parse(after)
        if not lo or not hi:
            actions["unbracketable"].append((number, None, pages, "unparseable neighbour"))
            continue
        mid = lo + (hi - lo) / 2
        actions[BRACKETED].append((number, mid.strftime("%Y-%m-%d %H:%M:%S"), pages, None))

    return actions


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"jobs.db (default: {DEFAULT_DB})")
    ap.add_argument("--apply", action="store_true",
                    help="write the changes. Without this, nothing is modified.")
    ap.add_argument("--show", type=int, default=5, metavar="N",
                    help="example rows to print per group (default 5)")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"database not found: {args.db}\n  Pass --db, or set PRINTOSKY_DB.")

    conn = sqlite3.connect(args.db)
    try:
        actions = plan(conn)
    except sqlite3.OperationalError as exc:
        sys.exit(f"could not read konica_jobs: {exc}")

    print(f"database : {args.db}")
    print(f"mode     : {'APPLY — this will write' if args.apply else 'DRY RUN — nothing is changed'}\n")

    order = [PRINT_END, BRACKETED, QUARANTINED, "ambiguous", "unbracketable"]
    label = {
        PRINT_END:     "dated from print_end_date (recorded, not inferred)",
        BRACKETED:     "dated by bracketing between dated neighbours (inferred)",
        QUARANTINED:   "quarantined — fields shifted, columns untrustworthy",
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
