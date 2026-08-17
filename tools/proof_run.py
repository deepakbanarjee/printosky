#!/usr/bin/env python3
"""Impose one source PDF in every layout combination, for a proof run on paper.

Runs on the STORE PC, against the deployed `print_planner`. That is the whole
point: it exercises the same code path the counter uses, so a green result says
something about this machine rather than about the machine that wrote it.
Imposing elsewhere and carrying the PDFs over proves nothing.

    python tools/proof_run.py notes.pdf
    python tools/proof_run.py notes.pdf --out C:\\Printosky\\Proof
    python tools/proof_run.py notes.pdf --only T1 T2
    python tools/proof_run.py notes.pdf --send            # actually print
    python tools/proof_run.py notes.pdf --send --printer konica

Without ``--send`` nothing reaches a printer: the imposed PDFs are written to
the output folder and the expected layout of each is printed, so the geometry
can be checked on screen before any paper is committed.

Test IDs match docs/PRINT_ROTATION_MATRIX.md and the printed proof checklist.
T1 and T2 come first because they gate the rest -- if the turn direction is
wrong, T1 shows it and the other ten are wasted paper.
"""
import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402  PyMuPDF
import nup_imposer  # noqa: E402
import print_planner  # noqa: E402

#: (test id, n-up, orientation, fill direction). Ordered by how much each sheet
#: tells you, not by layout: T1 decides the turn direction, T2 is the one that
#: can contradict the Konica's recorded back rotation.
COMBINATIONS = [
    ("T1",  2, "landscape", "horizontal"),
    ("T2",  2, "portrait",  "horizontal"),
    ("T3",  1, "portrait",  "horizontal"),
    ("T4",  1, "landscape", "horizontal"),
    ("T5",  4, "portrait",  "horizontal"),
    ("T6",  4, "portrait",  "vertical"),
    ("T7",  4, "landscape", "horizontal"),
    ("T8",  4, "landscape", "vertical"),
    ("T9",  6, "portrait",  "horizontal"),
    ("T10", 6, "portrait",  "vertical"),
    ("T11", 6, "landscape", "horizontal"),
    ("T12", 6, "landscape", "vertical"),
]


def expected_layout(nup, orientation, direction, pages):
    """The front and back of sheet 1, as rows of page numbers, plus the angles."""
    cols, rows = nup_imposer.sheet_grid(nup)
    plan = nup_imposer.impose_plan(pages, nup=nup, orientation=orientation,
                                   is_duplex=True, direction=direction)

    def rows_of(side):
        grid = {(s["col"], s["row"]): s for s in side["slots"]}
        return [" ".join(str(grid[(c, r)]["page"] or "--").rjust(2)
                         for c in range(cols)) for r in range(rows)]

    front, back = plan[0], plan[1]
    return (rows_of(front), rows_of(back),
            front["slots"][0]["rotation"], back["slots"][0]["rotation"])


def build(test_id, nup, orientation, direction, src, out_dir, paper_size):
    """Impose one combination through the deployed planner. Returns a summary."""
    spec = {
        "nup": nup,
        "orientation": orientation,
        "nup_direction": direction,
        "sides": "duplex",
        "paper_size": paper_size,
        "colour_mode": "bw",
        "copies": 1,
    }
    actions, temp_dir = print_planner.plan_print_job(
        f"PROOF-{test_id}", src, spec, out_dir)
    try:
        action = actions[0]
        name = f"{test_id}_{nup}up_{orientation}_{direction}.pdf"
        dest = os.path.join(out_dir, name)
        shutil.copy(action["pdf_path"], dest)

        doc = fitz.open(dest)
        rect, sides = doc[0].rect, len(doc)
        doc.close()
    finally:
        # plan_print_job returns None for temp_dir on the pass-through path.
        print_planner.cleanup_temp_dir(temp_dir)

    return {
        "id": test_id, "nup": nup, "orientation": orientation,
        "direction": direction, "path": dest, "sides": sides,
        "width": rect.width, "height": rect.height,
        "portrait": rect.width < rect.height,
        "sides_token": action["sides"],
        "orientation_flag": action["orientation"],
    }


def report(result, pages):
    cols, rows = nup_imposer.sheet_grid(result["nup"])
    front, back, frot, brot = expected_layout(
        result["nup"], result["orientation"], result["direction"], pages)

    shape = "PORTRAIT" if result["portrait"] else "LANDSCAPE  <-- WRONG"
    print(f"\n{result['id']}  {result['nup']}-up {result['orientation']} "
          f"{result['direction']}   grid {cols}x{rows}")
    print(f"    sheet    : {result['width']:.0f}x{result['height']:.0f}pt {shape}")
    print(f"    output   : {result['sides']} sheet-sides "
          f"({(result['sides'] + 1) // 2} sheets), "
          f"sides={result['sides_token']!r} orientation={result['orientation_flag']!r}")
    for i, (f, b) in enumerate(zip(front, back)):
        lead = "    expected : " if i == 0 else "               "
        print(f"{lead}{f}      {b}")
    print(f"               front @{frot}deg        back @{brot}deg")
    if result["orientation"] == "landscape":
        print("               turn the sheet CLOCKWISE to read; page 1 at the bottom")
    print(f"    file     : {result['path']}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="source PDF — use a portrait doc with visible page numbers")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "proof_run"),
                    help="folder for the imposed PDFs (default: ./proof_run)")
    ap.add_argument("--paper", default="A4", help="paper size (default: A4)")
    ap.add_argument("--only", nargs="+", metavar="ID",
                    help="run only these test IDs, e.g. --only T1 T2")
    ap.add_argument("--send", action="store_true",
                    help="actually print each sheet. Without this, nothing is printed.")
    ap.add_argument("--printer", default="epson", choices=["epson", "konica"],
                    help="printer key (default: epson — Nattika has no Konica)")
    args = ap.parse_args()

    if not os.path.exists(args.source):
        sys.exit(f"source PDF not found: {args.source}")
    os.makedirs(args.out, exist_ok=True)

    doc = fitz.open(args.source)
    pages = len(doc)
    first = doc[0].rect
    doc.close()

    wanted = {t.upper() for t in args.only} if args.only else None
    combos = [c for c in COMBINATIONS if wanted is None or c[0] in wanted]
    if not combos:
        sys.exit(f"no test IDs matched {args.only}")

    print("Printosky proof run")
    print(f"  source  : {args.source}  ({pages} pages, "
          f"{first.width:.0f}x{first.height:.0f}pt)")
    print(f"  output  : {args.out}")
    print(f"  paper   : {args.paper}")
    print(f"  tests   : {', '.join(c[0] for c in combos)}")
    print("  Every sheet should leave as PORTRAIT with sides='ds'.")

    results = []
    for test_id, nup, orientation, direction in combos:
        try:
            result = build(test_id, nup, orientation, direction,
                           args.source, args.out, args.paper)
        except Exception as exc:                      # noqa: BLE001 — report and carry on
            print(f"\n{test_id}  {nup}-up {orientation} {direction}\n    FAILED: {exc}")
            continue
        results.append(result)
        report(result, pages)

    wrong = [r["id"] for r in results if not r["portrait"]]
    print(f"\n{len(results)} of {len(combos)} imposed into {args.out}")
    if wrong:
        print(f"  !! not portrait: {', '.join(wrong)} — old code still loaded? "
              f"check `git log --oneline -1` and restart Printosky")

    if not args.send:
        print("\nNothing printed. Check the geometry, then re-run with --send.")
        return

    # Import here so a dry run never touches printer config or store state.
    import print_server                                # noqa: PLC0415

    print(f"\nSending {len(results)} jobs to '{args.printer}' …")
    for r in results:
        ok, msg = print_server.send_to_printer(
            job_id=f"PROOF-{r['id']}",
            filepath=r["path"],
            printer_key=args.printer,
            copies=1,
            colour_mode="bw",
            sides=r["sides_token"],
            paper_size=args.paper,
            orientation=r["orientation_flag"],
            update_status=False,
        )
        print(f"  {r['id']:<4} {'sent' if ok else 'FAILED'}  {msg}")


if __name__ == "__main__":
    main()
