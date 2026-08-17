#!/usr/bin/env python3
"""Print every page-rotation combination the imposer can produce.

Enumerates 1-up / 2-up / 4-up / 6-up (and 9-up) duplex, for a portrait and a
landscape selection, in horizontal and vertical fill order, and reports for
each sheet-side which logical page lands in which physical slot and at what
angle.

Note that "portrait"/"landscape" below is the LAYOUT the customer asked for.
The sheet itself is always portrait -- the whole document goes to the printer
as portrait duplex.

Numbers come straight out of ``nup_imposer.impose_plan`` -- the same function
``perform_nup`` uses -- so this table is the imposer, not a description of it.

    python tools/nup_matrix.py                # readable table, every combination
    python tools/nup_matrix.py --nup 2        # one n-up only
    python tools/nup_matrix.py --simplex      # single-sided too
    python tools/nup_matrix.py --markdown     # the table as Markdown
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nup_imposer  # noqa: E402

NUPS = [1, 2, 4, 6, 9]
ORIENTATIONS = ["portrait", "landscape"]
DIRECTIONS = ["horizontal", "vertical"]


def combinations(nups, orientations, directions, duplex_modes):
    for nup in nups:
        for orientation in orientations:
            for direction in directions:
                for is_duplex in duplex_modes:
                    yield nup, orientation, direction, is_duplex


def slot_cells(side):
    """Render one sheet-side as rows of "page@angle", laid out as it prints."""
    grid = {(s["col"], s["row"]): s for s in side["slots"]}
    lines = []
    for row in range(side["rows"]):
        cells = []
        for col in range(side["cols"]):
            s = grid.get((col, row))
            if s is None or s["page"] is None:
                cells.append("  --   ")
            else:
                cells.append(f"{s['page']:>3}@{s['rotation']:<3}".ljust(7))
        lines.append(" ".join(cells))
    return lines


def describe(nup, orientation, direction, is_duplex, pages):
    plan = nup_imposer.impose_plan(
        total_pages=pages, nup=nup, orientation=orientation,
        is_duplex=is_duplex, direction=direction,
    )
    cols, rows = nup_imposer.sheet_grid(nup, orientation, direction)
    back = nup_imposer.back_rotation(orientation)
    head = (f"{nup}-up  {orientation:<9} {direction:<10} "
            f"{'duplex' if is_duplex else 'simplex':<8} "
            f"portrait sheet, grid {cols}x{rows}  back turn {back}deg")
    return head, plan


def render_text(nup, orientation, direction, is_duplex, pages):
    head, plan = describe(nup, orientation, direction, is_duplex, pages)
    out = [head, "-" * len(head)]
    for side in plan:
        label = side["side"].upper()
        body = slot_cells(side)
        prefix = f"  sheet {side['sheet']} [{label:<5}]  "
        out.append(prefix + body[0])
        for line in body[1:]:
            out.append(" " * len(prefix) + line)
    out.append("")
    return "\n".join(out)


def render_markdown_row(nup, orientation, direction, is_duplex, pages):
    _, plan = describe(nup, orientation, direction, is_duplex, pages)
    cols, rows = nup_imposer.sheet_grid(nup, orientation, direction)
    front = next(s for s in plan if s["side"] == "front")
    back = next((s for s in plan if s["side"] == "back"), None)

    def order(side):
        if side is None:
            return "—"
        placed = sorted(
            (s for s in side["slots"] if s["page"] is not None),
            key=lambda s: (s["row"], s["col"]),
        )
        return " ".join(f"{s['page']}@{s['rotation']}°" for s in placed) or "—"

    return (f"| {nup}-up | {orientation} | {direction} | {cols}×{rows} "
            f"| {order(front)} | {order(back)} "
            f"| {nup_imposer.back_rotation(orientation)}° |")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--nup", type=int, action="append",
                    help="restrict to one n-up (repeatable); default: all")
    ap.add_argument("--pages", type=int, default=None,
                    help="logical pages to lay out; default: two full sheets")
    ap.add_argument("--simplex", action="store_true",
                    help="include single-sided as well as duplex")
    ap.add_argument("--markdown", action="store_true",
                    help="emit a Markdown table instead of the readable layout")
    args = ap.parse_args()

    nups = args.nup or NUPS
    duplex_modes = [True, False] if args.simplex else [True]

    if args.markdown:
        print("Every sheet is portrait; the orientation column is the customer's choice.\n")
        print("| Layout | Orientation | Direction | Grid | Front side | Back side | Back turn |")
        print("|---|---|---|---|---|---|---|")
        for nup, orientation, direction, is_duplex in combinations(
                nups, ORIENTATIONS, DIRECTIONS, duplex_modes):
            pages = args.pages if args.pages else nup * 2
            print(render_markdown_row(nup, orientation, direction, is_duplex, pages))
        return

    print("Page rotation matrix — angles are applied to a PORTRAIT source page.")
    print("Cells read `page@angle`, laid out in the physical slot positions.")
    print("Every imposed sheet is PORTRAIT; the orientation named is the layout")
    print("the customer chose. The printer is always told `duplexlong`; the back")
    print("turn below is ours.\n")
    for nup, orientation, direction, is_duplex in combinations(
            nups, ORIENTATIONS, DIRECTIONS, duplex_modes):
        pages = args.pages if args.pages else nup * 2
        print(render_text(nup, orientation, direction, is_duplex, pages))


if __name__ == "__main__":
    main()
