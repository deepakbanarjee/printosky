#!/usr/bin/env python3
"""Build every page-scaling combination from one source PDF, for a proof on paper.

Runs on the STORE PC, against the deployed `print_planner` and `pdf_scaler`.
That is the whole point: it exercises the same code path the counter uses, so a
green result says something about this machine. Scaling elsewhere and carrying
the PDFs over proves nothing.

    python tools/scale_proof.py --make-source                     # no file needed
    python tools/scale_proof.py --make-source --only S3 S4
    python tools/scale_proof.py --make-source --only S3 S4 --send --printer konica
    python tools/scale_proof.py yourfile.pdf --only S3 S4         # or bring your own

The source must be A4 portrait with visible page numbers. `--make-source`
builds exactly that, using the same numbered pages tools/nup_final_test.py has
always built — so no one has to go hunting for a suitable PDF on a store PC
before they can run the proof.

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
from tools.nup_final_test import build_source  # noqa: E402
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
     "the WHOLE page turned, border unbroken on all four sides, page 1 at the "
     "bottom. An A4 page cannot be turned onto A4 at true size, so this is "
     "fitted and an alert says so — if any edge runs off the paper, that is "
     "the 2026-09-03 fault back again"),
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


def ink_box(page):
    """The bounding box of everything drawn on a page, or None if it is blank.

    Text blocks alone are not enough: on a page whose content has run off the
    sheet, most of the text is already gone and what is left looks innocent.
    The drawn border is what gives it away, so drawings, text and images are
    all unioned.
    """
    boxes = [fitz.Rect(d["rect"]) for d in page.get_drawings()]
    boxes += [fitz.Rect(b[:4]) for b in page.get_text("blocks")]
    for img in page.get_images(full=True):
        boxes += list(page.get_image_rects(img[0]))
    if not boxes:
        return None
    box = boxes[0]
    for b in boxes[1:]:
        box |= b
    return box


def measure(path):
    """Per-sheet geometry: the page box, the ink box, and whether ink escaped."""
    doc = fitz.open(path)
    try:
        out = []
        for page in doc:
            box = ink_box(page)
            escaped = bool(box) and (
                box.x0 < -1 or box.y0 < -1
                or box.x1 > page.rect.width + 1
                or box.y1 > page.rect.height + 1)
            out.append({"page": page.rect, "ink": box, "escaped": escaped})
        return out
    finally:
        doc.close()


def verify(results, paper):
    """Check the PDFs on disk before any paper is committed.

    This exists because the tool used to report `baked: yes` for S7 while the
    page ran 43mm off each edge and the title and footer left the sheet — a
    green line about a sheet nobody would accept. "It was applied" is not the
    same claim as "it is right", and only the second one is worth printing.

    Returns (verdicts, failed) where a verdict is (id, ok, note).
    """
    sheet_w, sheet_h = nup_imposer.portrait_sheet(paper)
    by_id = {r["id"]: r for r in results}
    verdicts = []

    for r in results:
        problems = []
        for i, m in enumerate(measure(r["path"]), 1):
            if m["ink"] is None:
                problems.append(f"sheet {i} is blank")
                continue
            if m["escaped"]:
                problems.append(f"sheet {i} draws outside the paper")
            if (abs(m["page"].width - sheet_w) > 1
                    or abs(m["page"].height - sheet_h) > 1):
                problems.append(f"sheet {i} is not portrait {paper}")
        verdicts.append((r["id"], not problems, "; ".join(problems) or "geometry sane"))

    # S6 is the one combination that is SUPPOSED to lose content at the edges,
    # so an escape there is the expected answer, not a fault.
    verdicts = [(i, True, "enlarged past the edges, as asked") if i == "S6" and not ok
                else (i, ok, note) for i, ok, note in verdicts]

    # The pair the whole proof turns on. Same content, two modes: if they match,
    # scaling did nothing and the other six sheets are wasted paper.
    if "S3" in by_id and "S4" in by_id:
        a, b = measure(by_id["S3"]["path"])[0]["ink"], measure(by_id["S4"]["path"])[0]["ink"]
        if a and b and a.width > 0:
            ratio = b.width / a.width
            ok = ratio > 1.15
            verdicts.append(("S3 vs S4", ok,
                             f"Fit is {ratio:.2f}x Actual"
                             + ("" if ok else " — TOO CLOSE, scaling is not being applied")))

    # A percentage that does not move the ink by that percentage is not applied.
    if "S2" in by_id:
        base = measure(by_id["S2"]["path"])[0]["ink"]
        for test_id, want in (("S5", 0.75), ("S6", 1.50)):
            if test_id in by_id and base and base.width > 0:
                got = measure(by_id[test_id]["path"])[0]["ink"]
                ratio = got.width / base.width
                ok = abs(ratio - want) < 0.06
                verdicts.append((f"{test_id} vs S2", ok,
                                 f"{ratio:.2f}x, wanted {want:.2f}x"))

    return verdicts, [v for v in verdicts if not v[1]]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?",
                    help="source PDF — A4 portrait, with visible page numbers. "
                         "Omit it and one is generated (see --make-source).")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "scale_proof"),
                    help="folder for the scaled PDFs (default: ./scale_proof)")
    ap.add_argument("--paper", default="A4", help="paper size (default: A4)")
    ap.add_argument("--only", nargs="+", metavar="ID", help="run only these IDs, e.g. --only S3 S4")
    ap.add_argument("--send", action="store_true",
                    help="actually print each sheet. Without this, nothing is printed.")
    ap.add_argument("--send-anyway", action="store_true",
                    help="print even though the geometry check failed. Only with a "
                         "reason — a failed check means the PDF is already wrong.")
    ap.add_argument("--printer", default="epson", choices=["epson", "konica"],
                    help="printer key (default: epson — Nattika has no Konica)")
    ap.add_argument("--make-source", type=int, metavar="PAGES", nargs="?", const=4,
                    help="generate a numbered A4 source of PAGES pages (default 4) "
                         "instead of supplying one")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    # A source is REQUIRED to be a real A4 portrait PDF with visible page
    # numbers, and hunting for one on a store PC is friction between a person
    # and a proof they should be running. So the tool can make its own — the
    # same numbered pages tools/nup_final_test.py has always built, which is
    # what the rotation matrix was verified against.
    if args.make_source or not args.source:
        pages = args.make_source or 4
        args.source = os.path.join(args.out, "_source.pdf")
        build_source("SCALE PROOF", pages, args.source)
        print(f"generated a {pages}-page A4 source: {args.source}\n")
    elif not os.path.exists(args.source):
        sys.exit(
            f"source PDF not found: {args.source}\n"
            "  Point it at any A4 portrait PDF with visible page numbers, or\n"
            "  run it with no file at all and one is generated:\n"
            f"    python {os.path.basename(__file__)} --make-source "
            + " ".join(f"--only {t}" for t in ([" ".join(args.only)] if args.only else []))
        )

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

    verdicts, failed = verify(results, args.paper)
    print("GEOMETRY CHECK — the PDFs on disk, before any paper")
    for test_id, ok, note in verdicts:
        print(f"  {'PASS' if ok else 'FAIL'}  {test_id:<10} {note}")
    print()

    if failed:
        print(f"!! {len(failed)} check(s) FAILED. Do not send this to the printer — "
              f"the PDFs in {args.out} are already wrong, so the paper will be too.")
        if args.send and not args.send_anyway:
            sys.exit("refusing to print. Re-run without --send to inspect, or "
                     "pass --send-anyway if you know why this is expected.")
    else:
        print("Every check passed. The geometry is right in the file; what "
              "reaches the paper is now the printer's half of the job.")

    if not args.send:
        print(f"\n{len(results)} PDFs written. Nothing printed — open them, then re-run "
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
