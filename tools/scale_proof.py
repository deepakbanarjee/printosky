#!/usr/bin/env python3
"""Build every page-scaling combination from one source PDF, for a proof on paper.

Runs on the STORE PC, against the deployed `print_planner` and `pdf_scaler`.
That is the whole point: it exercises the same code path the counter uses, so a
green result says something about this machine. Scaling elsewhere and carrying
the PDFs over proves nothing.

    python tools/scale_proof.py notes.pdf
    python tools/scale_proof.py notes.pdf --only S3 S4
    python tools/scale_proof.py notes.pdf --send                  # actually print
    python tools/scale_proof.py notes.pdf --send --printer konica

Without ``--send`` nothing reaches a printer: the scaled PDFs are written to the
output folder and what each should look like is printed, so the geometry can be
checked on screen before any paper is committed.

Test IDs match the scaling checklist in docs/PRINT_ROTATION_MATRIX.md. S3 and S4
come first because they are the pair that decides everything: an A5 page at
Actual size against the same at Fit is the only case where the two modes differ
visibly, and it is exactly what the customer preview promises. If those two
sheets look the same, the feature is broken and the other six are wasted paper.

The A5 source for S3/S4 is derived from the source PDF here, with plain PyMuPDF
rather than pdf_scaler — deriving it with the code under test would prove
nothing about the code under test.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402  PyMuPDF
import nup_imposer  # noqa: E402
import pdf_scaler  # noqa: E402
import print_planner  # noqa: E402

#: (id, source, mode, percent, orientation, sides, what to look for)
COMBINATIONS = [
    ("S3", "a5", "actual", None, "portrait", "ss",
     "an A5-sized block centred on the A4 sheet, wide white margins"),
    ("S4", "a5", "fit", None, "portrait", "ss",
     "the same content enlarged to fill the A4 — visibly BIGGER than S3"),
    ("S1", "src", "fit", None, "portrait", "ss",
     "fills the page, even ~7mm border, nothing cut off"),
    ("S2", "src", "actual", None, "portrait", "ss",
     "a no-op — should be indistinguishable from an ordinary print"),
    ("S5", "src", "custom", 75, "portrait", "ss",
     "three-quarter size, centred, nothing cut off"),
    ("S6", "src", "custom", 150, "portrait", "ss",
     "enlarged, edges cropped EVENLY on all four sides"),
    ("S7", "src", "actual", None, "landscape", "ss",
     "true size, turned per the rotation matrix — page 1 at the bottom"),
    ("S8", "src", "fit", None, "portrait", "ds",
     "duplex: backs must register with fronts, exactly as an unscaled job does"),
]


def derive_a5(source: str, out_dir: str) -> str:
    """An A5-paged copy of the source, built with plain PyMuPDF.

    Deliberately not pdf_scaler: S3/S4 are testing whether pdf_scaler places an
    A5 page correctly, so the A5 page itself must come from somewhere else.
    """
    a5_w, a5_h = nup_imposer.portrait_sheet("A5")
    src = fitz.open(source)
    out = fitz.open()
    try:
        for i in range(len(src)):
            page = out.new_page(width=a5_w, height=a5_h)
            page.show_pdf_page(fitz.Rect(0, 0, a5_w, a5_h), src, i)
        path = os.path.join(out_dir, "_source_A5.pdf")
        out.save(path)
        return path
    finally:
        src.close()
        out.close()


def build(test_id, source, mode, percent, orientation, sides, out_dir, paper):
    """Plan one combination through the real planner and keep its output."""
    spec = {
        "nup": 1,
        "sides": "duplex" if sides == "ds" else "simplex",
        "colour_mode": "bw",
        "paper_size": paper,
        "orientation": orientation,
        "scale": {"mode": mode, "percent": percent} if percent else {"mode": mode},
    }
    actions, temp_dir = print_planner.plan_print_job(
        f"SCALEPROOF-{test_id}", source, spec, out_dir)
    try:
        action = actions[0]
        kept = os.path.join(out_dir, f"{test_id}_{mode}{percent or ''}_{orientation}.pdf")
        with open(action["pdf_path"], "rb") as fh:
            data = fh.read()
        with open(kept, "wb") as fh:
            fh.write(data)
    finally:
        print_planner.cleanup_temp_dir(temp_dir)

    doc = fitz.open(kept)
    try:
        sizes = {(round(p.rect.width), round(p.rect.height)) for p in doc}
        pages = len(doc)
    finally:
        doc.close()

    with open(source, "rb") as fh:
        cropped = pdf_scaler.count_cropped_pages(fh.read(), mode, percent, paper)

    return {"id": test_id, "path": kept, "pages": pages, "sizes": sizes,
            "scaled": action["scale_applied"], "cropped": cropped,
            "sides": action["sides"], "orientation": action["orientation"]}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="source PDF — A4 portrait, with visible page numbers")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "scale_proof"),
                    help="folder for the scaled PDFs (default: ./scale_proof)")
    ap.add_argument("--paper", default="A4", help="paper size (default: A4)")
    ap.add_argument("--only", nargs="+", metavar="ID", help="run only these IDs, e.g. --only S3 S4")
    ap.add_argument("--send", action="store_true",
                    help="actually print each sheet. Without this, nothing is printed.")
    ap.add_argument("--printer", default="epson", choices=["epson", "konica"],
                    help="printer key (default: epson — Nattika has no Konica)")
    args = ap.parse_args()

    if not os.path.exists(args.source):
        sys.exit(f"source PDF not found: {args.source}")
    os.makedirs(args.out, exist_ok=True)

    wanted = {t.upper() for t in args.only} if args.only else None
    combos = [c for c in COMBINATIONS if wanted is None or c[0] in wanted]
    if not combos:
        sys.exit(f"no test IDs matched {args.only}")

    a5_source = derive_a5(args.source, args.out) if any(c[1] == "a5" for c in combos) else None

    print("Printosky scaling proof")
    print(f"  source : {args.source}")
    print(f"  output : {args.out}")
    print(f"  paper  : {args.paper}")
    print("  Every sheet leaves as PORTRAIT. Scaling is baked into the PDF —")
    print("  if a sheet is wrong, the PDF in the output folder is wrong too.\n")

    results = []
    for test_id, which, mode, percent, orientation, sides, expect in combos:
        src = a5_source if which == "a5" else args.source
        try:
            r = build(test_id, src, mode, percent, orientation, sides, args.out, args.paper)
        except Exception as exc:                       # noqa: BLE001 — report and carry on
            print(f"{test_id}  FAILED: {exc}\n")
            continue
        results.append(r)
        label = f"{mode} {percent}%" if percent else mode
        print(f"{r['id']}  {label:<12} {orientation:<9} {'duplex' if sides == 'ds' else 'simplex'}")
        print(f"     look for : {expect}")
        print(f"     baked    : {'yes' if r['scaled'] else 'NO — nothing was applied'}"
              f"   sheets: {r['pages']}   size: {sorted(r['sizes'])}")
        if r["cropped"]:
            print(f"     cropping : {r['cropped']} page(s) lose content — expected for S6 only")
        print()

    not_baked = [r["id"] for r in results if not r["scaled"] and r["id"] != "S2"]
    if not_baked:
        print(f"!! nothing was baked for: {', '.join(not_baked)} — old code still "
              f"loaded? check `git log --oneline -1` and restart Printosky\n")

    if not args.send:
        print(f"{len(results)} PDFs written. Nothing printed — open them, then re-run "
              f"with --send.")
        return

    import print_server                                # noqa: PLC0415

    print(f"Sending {len(results)} jobs to '{args.printer}' …")
    for r in results:
        ok, msg = print_server.send_to_printer(
            job_id=f"SCALEPROOF-{r['id']}",
            filepath=r["path"],
            printer_key=args.printer,
            copies=1,
            colour_mode="bw",
            sides=r["sides"],
            paper_size=args.paper,
            orientation=r["orientation"],
            update_status=False,
            scale_applied=r["scaled"],
        )
        print(f"  {r['id']:<4} {'sent' if ok else 'FAILED'}  {msg}")



if __name__ == "__main__":
    main()
