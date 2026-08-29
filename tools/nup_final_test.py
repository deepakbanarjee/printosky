#!/usr/bin/env python3
"""Final N-up acceptance test — every layout, on paper, each sheet self-labelled.

Runs on a STORE PC against the deployed ``print_planner`` + ``print_server``,
so a green stack of paper says something about *this machine and this printer*,
not about the machine that wrote the code. That is the whole point of a paper
test — imposing elsewhere and carrying PDFs over proves nothing.

For every combination of layout (1/2/4/6/9-up), orientation (portrait/landscape)
and fill direction it:

  1. builds a source PDF whose every page carries the **combination name** as a
     header plus a big page number, so after imposition the name appears in
     every cell of the sheet — you can never mix up which sheet is which;
  2. imposes it through ``print_planner.plan_print_job`` (the real path);
  3. with ``--send``, prints it, passing the combination name as the spooler
     job name too.

    python tools/nup_final_test.py                       # dry run, writes PDFs
    python tools/nup_final_test.py --send --printer epson
    python tools/nup_final_test.py --send --printer konica
    python tools/nup_final_test.py --only 9up_landscape_v --send --printer epson

"All three printers" means: run ``--send --printer konica`` and
``--send --printer epson`` on the OSP box, and ``--send --printer epson`` on the
Nattika box. No single box can reach all three.

Every imposed sheet must leave as PORTRAIT with sides='ds' — same invariant the
rotation matrix and ``test_print_planner`` hold. Read docs/PRINT_ROTATION_MATRIX.md
before trusting or changing anything here.
"""
import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402  PyMuPDF
import print_planner  # noqa: E402

#: (id, n-up, orientation, fill direction). 1-up and 2-up have a single grid
#: axis so fill direction is meaningless for them; 4/6/9-up carry both. This is
#: the full acceptance matrix — 16 combinations.
COMBINATIONS = [
    ("1up_portrait",    1, "portrait",  "horizontal"),
    ("1up_landscape",   1, "landscape", "horizontal"),
    ("2up_portrait",    2, "portrait",  "horizontal"),
    ("2up_landscape",   2, "landscape", "horizontal"),
    ("4up_portrait_h",  4, "portrait",  "horizontal"),
    ("4up_portrait_v",  4, "portrait",  "vertical"),
    ("4up_landscape_h", 4, "landscape", "horizontal"),
    ("4up_landscape_v", 4, "landscape", "vertical"),
    ("6up_portrait_h",  6, "portrait",  "horizontal"),
    ("6up_portrait_v",  6, "portrait",  "vertical"),
    ("6up_landscape_h", 6, "landscape", "horizontal"),
    ("6up_landscape_v", 6, "landscape", "vertical"),
    ("9up_portrait_h",  9, "portrait",  "horizontal"),
    ("9up_portrait_v",  9, "portrait",  "vertical"),
    ("9up_landscape_h", 9, "landscape", "horizontal"),
    ("9up_landscape_v", 9, "landscape", "vertical"),
]


def title_of(combo_id, nup, orientation, direction):
    """Human-readable label printed as the header on every page."""
    who = f"{nup}-UP {orientation.upper()}"
    if nup >= 4:
        who += f" {direction[0].upper()}"   # H / V
    return who


def build_source(title, pages, path):
    """A source PDF of ``pages`` A4-portrait pages. Each page shows the combo
    title as a header and a huge centred page number, so the label survives
    down to the smallest 9-up cell and page ORDER is checkable on the sheet."""
    doc = fitz.open()
    W, H = 595.28, 841.89
    for i in range(pages):
        page = doc.new_page(width=W, height=H)
        page.insert_textbox(
            fitz.Rect(20, 24, W - 20, 70), title,
            fontsize=26, fontname="hebo", align=fitz.TEXT_ALIGN_CENTER)
        # Big centred page number via insert_text (a baseline draw that never
        # silently drops the glyph the way an overflowing insert_textbox does).
        num, size = str(i + 1), 240
        tw = fitz.get_text_length(num, fontname="hebo", fontsize=size)
        page.insert_text(((W - tw) / 2, H / 2 + size * 0.35), num,
                         fontsize=size, fontname="hebo")
        page.insert_textbox(
            fitz.Rect(20, H - 60, W - 20, H - 24), f"{title}  ·  page {i + 1}",
            fontsize=14, align=fitz.TEXT_ALIGN_CENTER)
        # A border so cropping/overlap at the sheet edge is obvious.
        page.draw_rect(fitz.Rect(8, 8, W - 8, H - 8), width=1)
    doc.save(path)
    doc.close()


def build(combo, src_dir, out_dir, paper_size, copies):
    """Build one combination's labelled source, impose it, return a summary."""
    combo_id, nup, orientation, direction = combo
    title = title_of(*combo)
    # One duplex sheet exactly: nup on the front, nup on the back.
    pages = nup * 2
    src = os.path.join(src_dir, f"src_{combo_id}.pdf")
    build_source(title, pages, src)

    spec = {
        "nup": nup, "orientation": orientation, "nup_direction": direction,
        "sides": "duplex", "paper_size": paper_size, "colour_mode": "bw",
        "copies": copies,
    }
    actions, temp_dir = print_planner.plan_print_job(
        f"NUPTEST-{title}", src, spec, out_dir)
    try:
        action = actions[0]
        dest = os.path.join(out_dir, f"{combo_id}.pdf")
        shutil.copy(action["pdf_path"], dest)
        doc = fitz.open(dest)
        rect, sheet_sides = doc[0].rect, len(doc)
        doc.close()
    finally:
        print_planner.cleanup_temp_dir(temp_dir)

    return {
        "id": combo_id, "title": title, "nup": nup, "orientation": orientation,
        "direction": direction, "path": dest, "sheet_sides": sheet_sides,
        "width": rect.width, "height": rect.height,
        "portrait": rect.width < rect.height,
        "sides_token": action["sides"], "orientation_flag": action["orientation"],
        "copies": action["copies"], "colour_mode": action["colour_mode"],
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "nup_final_test"),
                    help="folder for the built PDFs (default: ./nup_final_test)")
    ap.add_argument("--paper", default="A4", help="paper size (default: A4)")
    ap.add_argument("--copies", type=int, default=1, help="copies per sheet (default: 1)")
    ap.add_argument("--only", nargs="+", metavar="ID",
                    help="run only these combination ids, e.g. --only 9up_landscape_v")
    ap.add_argument("--send", action="store_true",
                    help="actually print each combination. Without this, nothing prints.")
    ap.add_argument("--printer", default="epson", choices=["epson", "konica"],
                    help="printer key (default: epson — Nattika has no Konica)")
    args = ap.parse_args()

    src_dir = os.path.join(args.out, "_src")
    os.makedirs(src_dir, exist_ok=True)

    wanted = {t.lower() for t in args.only} if args.only else None
    combos = [c for c in COMBINATIONS if wanted is None or c[0] in wanted]
    if not combos:
        sys.exit(f"no combination ids matched {args.only}")

    print("Printosky N-up final test")
    print(f"  output : {args.out}")
    print(f"  paper  : {args.paper}   copies: {args.copies}")
    print(f"  combos : {', '.join(c[0] for c in combos)}")
    print("  Every sheet must leave as PORTRAIT with sides='ds'.\n")

    results = []
    for combo in combos:
        try:
            r = build(combo, src_dir, args.out, args.paper, args.copies)
        except Exception as exc:                       # noqa: BLE001 — report, carry on
            print(f"  {combo[0]:<16} FAILED to impose: {exc}")
            continue
        shape = "PORTRAIT" if r["portrait"] else "LANDSCAPE  <-- WRONG"
        print(f"  {r['id']:<16} {r['title']:<22} "
              f"{r['width']:.0f}x{r['height']:.0f}pt {shape}  "
              f"{r['sheet_sides']} sides, sides={r['sides_token']!r} "
              f"orientation={r['orientation_flag']!r}")
        results.append(r)

    wrong = [r["id"] for r in results if not r["portrait"]]
    print(f"\n  {len(results)}/{len(combos)} imposed into {args.out}")
    if wrong:
        print(f"  !! not portrait: {', '.join(wrong)} — stale code? "
              f"check `git log --oneline -1` and restart Printosky")

    if not args.send:
        print("\nNothing printed. Check the geometry on screen, then re-run with --send.")
        return

    import print_server                                 # noqa: PLC0415 — dry run stays clean

    print(f"\nSending {len(results)} combinations to '{args.printer}' …")
    for r in results:
        ok, msg = print_server.send_to_printer(
            job_id=r["title"],                          # combination name = spooler job name
            filepath=r["path"], printer_key=args.printer,
            copies=r["copies"], colour_mode="bw", sides=r["sides_token"],
            paper_size=args.paper, orientation=r["orientation_flag"],
            update_status=False,
        )
        print(f"  {r['id']:<16} {'sent' if ok else 'FAILED'}  {msg}")


if __name__ == "__main__":
    main()
