#!/usr/bin/env python3
"""What result codes has this Konica actually produced?

    python tools/konica_result_codes.py

Exists because the answer was needed at a counter and the only way to get it was
a Python one-liner pasted into PowerShell — where `\\"` ends a string rather than
escaping a quote, and `COUNT(*)` gets parsed as a command. Two rounds of quoting
errors to run one SELECT. A file has no quoting.

Reads only. Prints every distinct (result, job_type) with its volume and date
range, and says which codes `konica_normalize` can map.

Why it matters: an unmapped result is excluded from every completed-work count,
so the copy/scan reconciliation reads a SMALLER gap than reality.

But an unmappable value is not automatically a code. Run on OSP 2026-09-03, this
found 49 rows whose `result` held a FILENAME — `'Optical Instruments).pdf'`,
`'MAP STUDY'`, `'X2'` — every one with a NULL `job_date`. A comma inside a
filename had split the row and shifted every field left. Adding those to
RESULT_ALIASES would have enshrined corrupt data as a valid status, which is
what the first version of this tool advised.

So the shape is what tells them apart, and `job_date` is the tell:

    unmapped + NULL job_date  ->  a corrupt row. Fields are misaligned; the
                                  value is wreckage, not a code. Quarantine it.
    unmapped + a real date    ->  a genuine code this build has not been taught.
                                  `printed` then says whether it is a success
                                  variant (map to 'No Error') or a failure.
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_DB = os.environ.get("PRINTOSKY_DB", r"C:\Printosky\Data\jobs.db")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"jobs.db (default: {DEFAULT_DB})")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"database not found: {args.db}\n"
                 "  Pass --db, or set PRINTOSKY_DB.")

    # A stub that answers "yes, mapped" to everything would be the same
    # reassuring lie this tool exists to expose. If the module will not import,
    # the honest output is no verdict at all.
    try:
        from konica_normalize import is_known_result
    except Exception as exc:
        print(f"note: konica_normalize did not import ({exc}).\n"
              "      Volumes below are still accurate; the 'mapped' column is "
              "left blank because nothing here can answer it.\n")
        is_known_result = None

    conn = sqlite3.connect(args.db)
    try:
        rows = conn.execute("""
            SELECT COALESCE(result, '(null)'), COALESCE(job_type, '(null)'),
                   COUNT(1), SUM(COALESCE(pages_printed, 0)),
                   SUM(COALESCE(num_pages, 0)), MIN(job_date), MAX(job_date)
              FROM konica_jobs
          GROUP BY 1, 2
          ORDER BY 3 DESC
        """).fetchall()
    except sqlite3.OperationalError as exc:
        sys.exit(f"could not read konica_jobs: {exc}")
    finally:
        conn.close()

    print(f"database : {args.db}")
    print(f"{'result':<30}{'type':<9}{'jobs':>7}{'printed':>9}{'pages':>8}  "
          f"{'mapped':<7}{'first seen':<21}last seen")
    print("-" * 114)
    unmapped, corrupt = [], []
    for result, job_type, n, printed, pages, first, last in rows:
        if is_known_result is None:
            mark = "?"
        elif is_known_result(result):
            mark = "yes"
        elif first is None and last is None:
            # No date at all: the row is misaligned, not mislabelled.
            mark = "junk"
            corrupt.append((result, n))
        else:
            mark = "NO"
            unmapped.append((result, n, printed))
        # Truncate rather than let a long value shunt every later column right.
        label = result if len(result) <= 29 else result[:26] + "..."
        print(f"{label:<30}{job_type:<9}{n:>7}{printed:>9}{pages:>8}  "
              f"{mark:<7}{str(first):<21}{last}")

    if is_known_result is None:
        print("\nCannot say which codes are mapped from here — run this on a "
              "store PC, or from the repo root.")
        return
    if corrupt:
        total = sum(n for _, n in corrupt)
        print(f"\n{total} CORRUPT row(s) in {len(corrupt)} shapes — a filename or "
              "other text is sitting in `result`, and there is no job_date at all.")
        print("  The fields are misaligned; these are wreckage, not status codes.")
        print("  Do NOT add them to RESULT_ALIASES. They want deleting, and the "
              "import that produced them wants fixing.")
        for result, n in corrupt[:10]:
            print(f"    {result!r}: {n} row(s)")
        if len(corrupt) > 10:
            print(f"    …and {len(corrupt) - 10} more")

    if not unmapped:
        if not corrupt:
            print("\nEvery code is mapped. Nothing is being excluded by accident.")
        return

    print(f"\n{len(unmapped)} genuine code(s) this build cannot map:")
    for result, n, printed in unmapped:
        verdict = ("pages were printed — looks like a SUCCESS variant, so it "
                   "belongs in RESULT_ALIASES as 'No Error'"
                   if printed else
                   "nothing printed — looks like a FAILURE or cancellation")
        print(f"  {result!r}: {n} job(s), {printed} pages printed — {verdict}")
    print("\nSend this to whoever maintains konica_normalize.RESULT_ALIASES.")


if __name__ == "__main__":
    main()
