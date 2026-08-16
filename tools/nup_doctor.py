"""nup_doctor.py — show exactly what this PC would print for an N-up job.

Written because four rounds of "print it and tell me what it looked like"
produced almost no information. This reports what the code actually does,
on this machine, from the real code paths — so the only remaining unknown is
the printer itself.

It does NOT print. It reports, and saves the imposed PDF so it can be looked
at (or sent on) directly.

Usage:
    python tools/nup_doctor.py                       (generates its own source)
    python tools/nup_doctor.py --nup 4 --simplex
    python tools/nup_doctor.py <file.pdf> --nup 2
    python tools/nup_doctor.py --job OSKY-20260816-0001    (use a real job row)

With no file it builds a labelled test document itself, so there is nothing to
copy onto the store PC first.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("PyMuPDF is not installed on this PC — run: pip install PyMuPDF")

import print_planner  # noqa: E402
import nup_imposer    # noqa: E402


LINE = "─" * 68
ANGLES = {(1.0, 0.0): 0, (0.0, -1.0): 90, (-1.0, 0.0): 180, (0.0, 1.0): 270}


def _git(*args) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ).decode().strip()
    except Exception:
        return "unknown"


def _make_source(path: str, pages: int = 8) -> str:
    """A labelled test document: big page number, and a bar along the page's
    own TOP edge so the imposed sheet shows where each page's top ended up."""
    doc = fitz.open()
    for n in range(1, pages + 1):
        pg = doc.new_page(width=595.28, height=841.89)
        colour = (0.76, 0.09, 0.43) if n % 2 else (0.0, 0.47, 0.62)
        pg.draw_rect(fitz.Rect(0, 0, 595.28, 34), color=None, fill=colour)
        pg.insert_text((22, 23), "T O P   O F   P A G E", fontsize=15,
                       fontname="hebo", color=(1, 1, 1))
        pg.insert_text((220, 470), str(n), fontsize=260, fontname="hebo",
                       color=(0.07, 0.15, 0.35))
        pg.insert_text((22, 600), f"page {n} of {pages}", fontsize=20,
                       fontname="hebo", color=colour)
    doc.save(path)
    doc.close()
    return path


def _describe(path: str):
    """Per sheet-side: page labels found, their positions and rotations."""
    out = []
    with fitz.open(path) as doc:
        for pg in doc:
            items = []
            for blk in pg.get_text("dict")["blocks"]:
                for ln in blk.get("lines", []):
                    txt = "".join(s["text"] for s in ln["spans"]).strip()
                    # Only short labels — a page number or a marker word. Body
                    # text would bury the one thing this report is for.
                    if not txt or len(txt) > 4:
                        continue
                    d = (round(ln["dir"][0], 1), round(ln["dir"][1], 1))
                    items.append((round(ln["bbox"][1]), round(ln["bbox"][0]),
                                  txt, ANGLES.get(d, "?")))
            items.sort()
            out.append((pg.rect.width, pg.rect.height, items))
    return out


def _calibrate(args, current: int) -> int:
    """Two sheets, one duplex print, and the answer is unambiguous."""
    src = os.path.join(os.path.dirname(os.path.abspath(args.out)) or ".",
                       "nup_doctor_calibration_source.pdf")
    _make_source(src, 4)
    with open(src, "rb") as fh:
        src_bytes = fh.read()

    out = fitz.open()
    for label, rotation in (("A", 0), ("B", 180)):
        imposed = nup_imposer.perform_nup(
            src_bytes, cols=1, rows=2, paper_size=args.paper,
            orientation="Portrait", is_duplex=True,
            layout_direction="horizontal", back_rotation=rotation)
        sheet = fitz.open("pdf", imposed.getvalue())
        for n, pg in enumerate(sheet):
            band = (0.11, 0.42, 0.29) if label == "B" else (0.35, 0.38, 0.40)
            pg.draw_rect(fitz.Rect(0, 0, pg.rect.width, 30), color=None, fill=band)
            pg.insert_text((14, 21),
                           f"SHEET {label}   ({'FRONT' if n % 2 == 0 else 'BACK'})"
                           f"   duplex_back_rotation = {rotation}",
                           fontsize=12, fontname="hebo", color=(1, 1, 1))
        out.insert_pdf(sheet)
        sheet.close()
    out.save(args.out)
    out.close()
    os.remove(src)

    print(LINE)
    print("  CALIBRATION SHEET")
    print(LINE)
    print(f"  saved -> {os.path.abspath(args.out)}")
    print()
    print("  1. Print it: DUPLEX, LONG EDGE, portrait, 2 sheets.")
    print("  2. Turn each sheet over like a book (flip about the LEFT edge).")
    print("  3. Exactly one sheet reads 1,2 then 3,4 the right way up.")
    print()
    print("     sheet A reads correctly  ->  duplex_back_rotation = 0")
    print("     sheet B reads correctly  ->  duplex_back_rotation = 180")
    print()
    print(f"  Currently configured: {current}")
    print("  To change it, set this in store_config.json and restart:")
    print('      "duplex_back_rotation": 0     (or 180)')
    print(LINE)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", nargs="?", help="PDF to impose")
    ap.add_argument("--job", help="read settings from a real jobs/print_items row")
    ap.add_argument("--nup", type=int, default=2)
    ap.add_argument("--simplex", action="store_true")
    ap.add_argument("--paper", default="A4")
    ap.add_argument("--direction", default="horizontal")
    ap.add_argument("--scale", default="fit")
    ap.add_argument("--pages", type=int, default=8,
                    help="pages in the generated source (default 8)")
    ap.add_argument("--calibrate", action="store_true",
                    help="emit a 2-sheet A/B test that names the correct "
                         "duplex_back_rotation in one duplex print")
    ap.add_argument("-o", "--out", default="nup_doctor_output.pdf")
    args = ap.parse_args()

    print(LINE)
    print("  N-UP DOCTOR")
    print(LINE)
    print(f"  branch   : {_git('rev-parse', '--abbrev-ref', 'HEAD')}")
    print(f"  commit   : {_git('rev-parse', '--short', 'HEAD')}")
    print(f"  planner  : {print_planner.__file__}")
    print(f"  imposer  : {nup_imposer.__file__}")

    # Is this PC running code that has the portrait-canvas rule?
    with open(print_planner.__file__, encoding="utf-8") as fh:
        has_rule = "PORTRAIT CANVAS RULE" in fh.read()
    print(f"  portrait-canvas rule present : {'YES' if has_rule else 'NO — STALE CODE'}")

    try:
        from store_config import get_store_config
        cfg_rotation = get_store_config().duplex_back_rotation
    except Exception as exc:
        cfg_rotation = 0
        print(f"  store_config unreadable ({exc}) — assuming 0")
    print(f"  duplex_back_rotation : {cfg_rotation}  "
          f"(the one calibration constant; same for every printer)")

    if args.calibrate:
        return _calibrate(args, cfg_rotation)

    src = args.pdf
    nup, sides, paper = args.nup, ("ss" if args.simplex else "ds"), args.paper

    if args.job:
        import sqlite3
        from store_config import get_store_config
        db = get_store_config().db_path
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        job = conn.execute("SELECT * FROM jobs WHERE job_id=?", (args.job,)).fetchone()
        item = conn.execute(
            "SELECT * FROM print_items WHERE job_id=? ORDER BY item_number LIMIT 1",
            (args.job,)).fetchone()
        conn.close()
        if not job:
            return print(f"  job {args.job} not found in {db}") or 1
        src = job["filepath"] or src
        paper = (job["size"] if "size" in job.keys() else None) or "A4"
        if item:
            nup = int(str(item["layout"] or "1").split("-")[0] or 1)
            sides = item["sides"] or "ss"
        print(f"  job row  : {args.job}  layout={nup}-up sides={sides} paper={paper}")

    if not src:
        src = os.path.join(os.path.dirname(os.path.abspath(args.out)) or ".",
                           "nup_doctor_source.pdf")
        _make_source(src, args.pages)
        print(f"  generated: {os.path.basename(src)} ({args.pages} labelled pages)")
    if not os.path.exists(src):
        return print(f"  file not found: {src}") or 1

    with fitz.open(src) as doc:
        print(f"  source   : {os.path.basename(src)} — {len(doc)} pages, "
              f"{doc[0].rect.width:.0f}x{doc[0].rect.height:.0f}pt")

    spec = {
        "nup": nup, "nup_direction": args.direction,
        "sides": "duplex" if sides == "ds" else "simplex",
        "colour_mode": "bw", "paper_size": paper,
        "copies": 1, "scale_mode": args.scale,
    }

    print(LINE)
    print(f"  spec     : {spec}")

    dest = os.path.dirname(os.path.abspath(args.out)) or "."
    actions, temp_dir = print_planner.plan_print_job("NUP-DOCTOR", src, spec, dest)

    for i, a in enumerate(actions, 1):
        print(LINE)
        print(f"  ACTION {i} of {len(actions)}")
        print(f"    colour_mode : {a['colour_mode']}")
        print(f"    sides       : {a['sides']!r}   <- what the printer is told")
        print(f"    paper_size  : {a['paper_size']!r}")
        print(f"    orientation : {a['orientation']!r}")
        warn = a.get("print_area_warning")
        print(f"    print area  : {'OVERFLOWS by %.1f%%' % warn['overflow_pct'] if warn else 'fits'}")

        path = a["pdf_path"]
        if path == src:
            print("    imposed     : NO — the original file is printed as-is")
            continue

        sheets = _describe(path)
        w, h = sheets[0][0], sheets[0][1]
        print(f"    imposed     : {len(sheets)} sheet-sides, {w:.0f}x{h:.0f}pt "
              f"({'PORTRAIT' if h > w else 'LANDSCAPE'})")
        for n, (_, _, items) in enumerate(sheets, 1):
            side = "FRONT" if n % 2 else "BACK "
            labels = " ".join(f"{t[2]}@{t[3]}deg" for t in items) or "(blank)"
            print(f"      side {n} [{side}]: {labels}")

        if i == 1:
            import shutil
            shutil.copy(path, args.out)
            print(f"\n    saved -> {os.path.abspath(args.out)}")
            print("    Open it. What you see here is what goes to the printer;")
            print("    anything different on paper is the printer or its queue.")

    print_planner.cleanup_temp_dir(temp_dir)
    print(LINE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
