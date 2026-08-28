"""
foil_calibration.py — Print one sheet, foil it, and stop guessing.

Usage:
    python foil_calibration.py                    # -> foil_calibration.pdf
    python foil_calibration.py sheet.pdf --lpi 35,45,55,85

Every number in docs/FOILING.md — the 0.30mm minimum stroke, the 55 lpi ceiling
— is a starting point borrowed from other people's machines. This sheet replaces
them with yours. Print it on the Konica on the stock you actually use, foil it
the way you actually foil, then read off what survived and write those numbers
into docs/FOILING.md and MIN_STROKE_MM in foil_prep.py.

Do it once per foil type, and again if the laminator is serviced or replaced.

Construction note: strokes and type are vector so their widths are exact — a
0.15mm line rasterised at 600 dpi would be 15% off, which is the difference
between a pass and a fail on the row that matters. Only the halftone patches are
raster, at 600 dpi, because the whole point is to control the screen ruling
rather than let the RIP pick one.
"""

import argparse
import io
import os

import fitz
import numpy as np
from PIL import Image

MM = 72 / 25.4                      # points per millimetre
BLACK = (0, 0, 0)
WHITE = (1, 1, 1)

PAGE_W_MM = 210
PAGE_H_MM = 297
MARGIN_MM = 11
BLOCK_GAP_MM = 3.5

# The rows the sheet exists to settle. Straddle the 0.30mm we currently assume.
LINE_WIDTHS_MM = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.75, 1.00]
GAP_WIDTHS_MM = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
DOT_DIAMETERS_MM = [0.20, 0.30, 0.40, 0.50, 0.75, 1.00, 1.50]
SMALL_SIZES_PT = [6, 8, 10]
LARGE_SIZES_PT = [12, 16]
HALFTONE_LPI = [35, 45, 55, 85]
HALFTONE_TONES = [25, 50, 75]

SPECIMEN = 'Handgloves'
SERIF, SANS, BOLD = 'tiro', 'helv', 'hebo'


# ---------------------------------------------------------------------------
# Halftone screening
# ---------------------------------------------------------------------------

def halftone_patch(tone_pct: int, lpi: int, width_mm: float, height_mm: float,
                   dpi: int = 600, angle_deg: float = 45.0) -> bytes:
    """
    One clustered-dot AM halftone patch as a 1-bit PNG.

    The classic analytic screen: a dot function that rises with distance from
    each screen-cell centre, so dots grow round from their centres the way a RIP
    screens them. Angled 45 degrees, as K always is — the angle is what stops the
    dot grid reading as a plaid.

    The cut is taken at the tone's quantile of the dot field rather than at the
    tone itself, so a patch labelled 50% really does carry 50% ink. Otherwise the
    labels on the sheet would be off by several points and the sheet would be
    calibrating against its own error.
    """
    w = max(1, int(round(width_mm / 25.4 * dpi)))
    h = max(1, int(round(height_mm / 25.4 * dpi)))
    cell = dpi / lpi                                  # pixels per halftone cell

    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    theta = np.deg2rad(angle_deg)
    u = (x * np.cos(theta) + y * np.sin(theta)) / cell
    v = (-x * np.sin(theta) + y * np.cos(theta)) / cell

    dot = (2 - np.cos(2 * np.pi * u) - np.cos(2 * np.pi * v)) / 4
    ink = dot < np.quantile(dot, tone_pct / 100.0)

    buf = io.BytesIO()
    Image.fromarray(np.where(ink, 0, 255).astype(np.uint8), 'L').convert('1').save(
        buf, 'PNG', optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Drawing helpers — everything in millimetres, origin top-left
# ---------------------------------------------------------------------------

def pt_to_mm(pt: float) -> float:
    return pt / MM


class Sheet:
    """Thin mm-based wrapper over a fitz page. Keeps the layout code readable."""

    def __init__(self, page):
        self.page = page

    def rect(self, x, y, w, h, fill=BLACK):
        self.page.draw_rect(fitz.Rect(x * MM, y * MM, (x + w) * MM, (y + h) * MM),
                            color=None, fill=fill)

    def line(self, x1, y1, x2, y2, width_mm, color=BLACK):
        self.page.draw_line(fitz.Point(x1 * MM, y1 * MM), fitz.Point(x2 * MM, y2 * MM),
                            color=color, width=width_mm * MM)

    def circle(self, cx, cy, diameter_mm, fill=BLACK):
        self.page.draw_circle(fitz.Point(cx * MM, cy * MM), diameter_mm / 2 * MM,
                              color=None, fill=fill)

    def text(self, x, baseline, s, size=7, font=SANS, color=BLACK):
        self.page.insert_text(fitz.Point(x * MM, baseline * MM), s, fontsize=size,
                              fontname=font, color=color)

    def text_width(self, s, size, font=SANS) -> float:
        return pt_to_mm(fitz.get_text_length(s, fontname=font, fontsize=size))

    def image(self, x, y, w, h, stream):
        self.page.insert_image(fitz.Rect(x * MM, y * MM, (x + w) * MM, (y + h) * MM),
                               stream=stream)

    def title(self, x, y, w, label) -> float:
        """Block heading plus a rule. Returns the y the block's content starts at."""
        self.text(x, y + 2.8, label, size=8, font=BOLD)
        self.line(x, y + 4.4, x + w, y + 4.4, 0.4)
        return y + 4.4


# ---------------------------------------------------------------------------
# Blocks — each draws itself from (x, y) and returns the height it used
# ---------------------------------------------------------------------------

def block_lines(s: Sheet, x, y, w) -> float:
    """A — positive strokes, three directions each. Foil skips lengthwise first."""
    top = s.title(x, y, w, 'A  POSITIVE LINES')
    row_y = top + 4.5
    for mm in LINE_WIDTHS_MM:
        s.text(x, row_y + 0.9, f'{mm:.2f}', size=6)
        s.line(x + 9, row_y, x + 45, row_y, mm)                    # horizontal
        for i in range(6):                                          # vertical comb
            vx = x + 49 + i * 3
            s.line(vx, row_y - 2, vx, row_y + 2, mm)
        zx = x + 70
        for i in range(3):                                          # 45 degrees
            s.line(zx, row_y + 2, zx + 2, row_y - 2, mm)
            s.line(zx + 2, row_y - 2, zx + 4, row_y + 2, mm)
            zx += 4
        row_y += 4.5
    s.text(x, row_y + 1.5, 'mm', size=5.5)
    s.text(x + 9, row_y + 1.5, 'horizontal', size=5.5)
    s.text(x + 49, row_y + 1.5, 'vertical', size=5.5)
    s.text(x + 70, row_y + 1.5, '45°', size=5.5)
    return (row_y + 1.5) - y + 1.5


def block_reversed_lines(s: Sheet, x, y, w) -> float:
    """B — the same widths knocked out of a solid. Foil closes gaps before it skips."""
    top = s.title(x, y, w, 'B  REVERSED LINES  (white on solid)')
    panel_y = top + 1.5
    panel_h = 4.5 * len(LINE_WIDTHS_MM) + 3
    s.rect(x, panel_y, w, panel_h)
    row_y = panel_y + 4.5
    for mm in LINE_WIDTHS_MM:
        s.text(x + 1, row_y + 0.9, f'{mm:.2f}', size=6, color=WHITE)
        s.line(x + 9, row_y, x + 45, row_y, mm, color=WHITE)
        for i in range(6):
            vx = x + 49 + i * 3
            s.line(vx, row_y - 2, vx, row_y + 2, mm, color=WHITE)
        row_y += 4.5
    return (panel_y + panel_h) - y


def _specimen_line(s: Sheet, x, baseline, w, sizes, font, colour) -> None:
    """One row of type specimens, packed left to right, measured so nothing collides."""
    cx = x
    for size in sizes:
        label = f'{size}pt'
        s.text(cx, baseline, label, size=5.5, color=colour)
        cx += s.text_width(label, 5.5) + 1.2
        s.text(cx, baseline, SPECIMEN, size=size, font=font, color=colour)
        cx += s.text_width(SPECIMEN, size, font) + 3.5


def block_text(s: Sheet, x, y, w, reverse=False) -> float:
    """C/D — type specimens. The size that fails is your minimum point size."""
    label = 'D  REVERSED TEXT  (white on solid)' if reverse else 'C  POSITIVE TEXT'
    top = s.title(x, y, w, label)

    rows = [(SMALL_SIZES_PT, SERIF), (LARGE_SIZES_PT, SERIF),
            (SMALL_SIZES_PT, SANS), (LARGE_SIZES_PT, SANS)]
    leading = 2.0
    panel_h = sum(pt_to_mm(max(sizes)) + leading for sizes, _ in rows) + 4.5

    panel_y = top + 1.5
    if reverse:
        s.rect(x, panel_y, w, panel_h)
    colour = WHITE if reverse else BLACK

    baseline = panel_y + 1.5
    for sizes, font in rows:
        baseline += pt_to_mm(max(sizes))
        _specimen_line(s, x + 1.5, baseline, w, sizes, font, colour)
        baseline += leading
    s.text(x + 1.5, panel_y + panel_h - 1.5,
           'rows 1-2 serif (Times)   rows 3-4 sans (Helvetica)', size=5.5, color=colour)
    return (panel_y + panel_h) - y


def block_solids(s: Sheet, x, y, w) -> float:
    """E — large flats. Where foil goes mottled, and the reason to avoid big fills."""
    top = s.title(x, y, w, 'E  SOLIDS  (mottle test)')
    box_y = top + 2
    s.rect(x, box_y, 46, 33)
    s.rect(x + 50, box_y, 25, 25)
    s.rect(x + 78, box_y, 10, 10)
    s.text(x, box_y + 36, '46 x 33mm', size=5.5)
    s.text(x + 50, box_y + 36, '25mm', size=5.5)
    s.text(x + 78, box_y + 36, '10mm', size=5.5)
    return (box_y + 36) - y + 1.5


def block_gaps(s: Sheet, x, y, w) -> float:
    """F — bridging. Foil creeps sideways and welds neighbouring strokes shut."""
    top = s.title(x, y, w, 'F  GAPS  (bridging test)')
    row_y = top + 4.5
    for mm in GAP_WIDTHS_MM:
        s.text(x, row_y + 0.9, f'{mm:.2f}', size=6)
        bar_x = x + 9
        for _ in range(8):
            s.rect(bar_x, row_y - 2, 0.5, 4)
            bar_x += 0.5 + mm
        s.rect(x + 46, row_y - 2, 20, 4)
        s.rect(x + 66 + mm, row_y - 2, 20, 4)
        row_y += 4.5
    s.text(x, row_y + 1.5, 'mm', size=5.5)
    s.text(x + 9, row_y + 1.5, '0.5mm bars', size=5.5)
    s.text(x + 46, row_y + 1.5, 'two solids', size=5.5)
    return (row_y + 1.5) - y + 1.5


def block_dots(s: Sheet, x, y, w) -> float:
    """G — isolated marks, positive and knocked out. Full stops and counters."""
    top = s.title(x, y, w, 'G  DOTS  (isolated marks, positive and reversed)')
    half = (w - 6) / 2
    cy = top + 8
    pitch = half / (len(DOT_DIAMETERS_MM) + 0.5)

    dot_x = x + pitch * 0.75
    for mm in DOT_DIAMETERS_MM:
        s.circle(dot_x, cy, mm)
        s.text(dot_x - 2.5, cy + 6, f'{mm:.2f}', size=5.5)
        dot_x += pitch

    panel_x = x + half + 6
    s.rect(panel_x, top + 1.5, half, 15)
    hole_x = panel_x + pitch * 0.75
    for mm in DOT_DIAMETERS_MM:
        s.circle(hole_x, cy, mm, fill=WHITE)
        s.text(hole_x - 2.5, cy + 6, f'{mm:.2f}', size=5.5, color=WHITE)
        hole_x += pitch
    return (top + 16.5) - y


def block_halftones(s: Sheet, x, y, w, lpi_list, dpi) -> float:
    """H — screen ruling. Below the ruling that survives, tone foils as speckle."""
    top = s.title(x, y, w, 'H  HALFTONES  (screen ruling vs tone)')
    gutter = 11
    n = len(lpi_list)
    patch_w = (w - gutter - (n - 1) * 4) / n
    patch_h = 11
    grid_y = top + 5.5

    for col, lpi in enumerate(lpi_list):
        px = x + gutter + col * (patch_w + 4)
        label = f'{lpi} lpi'
        s.text(px + (patch_w - s.text_width(label, 6.5, BOLD)) / 2, grid_y - 1.2,
               label, size=6.5, font=BOLD)

    for row, tone in enumerate(HALFTONE_TONES):
        py = grid_y + row * (patch_h + 3.5)
        s.text(x, py + patch_h / 2 + 1, f'{tone}%', size=6.5, font=BOLD)
        for col, lpi in enumerate(lpi_list):
            px = x + gutter + col * (patch_w + 4)
            s.image(px, py, patch_w, patch_h,
                    halftone_patch(tone, lpi, patch_w, patch_h, dpi=dpi))
    return (grid_y + len(HALFTONE_TONES) * (patch_h + 3.5)) - y


def block_header(s: Sheet, x, y, w) -> float:
    s.text(x, y + 5.5, 'TTF FOIL CALIBRATION SHEET', size=15, font=BOLD)
    s.text(x, y + 10, 'Print on the Konica. Foil it the way you foil real work. '
                      'Then write what survived into docs/FOILING.md.', size=7)

    # Feed direction. The laminator has a grain; a sheet fed the other way can
    # read completely differently, so the sheet has to record which way it went.
    ax = x + w - 30
    s.text(ax, y + 3, 'FEED DIRECTION', size=5.5, font=BOLD)
    s.rect(ax, y + 5.4, 20, 1.2)
    s.page.draw_polyline([fitz.Point((ax + 20) * MM, (y + 3.4) * MM),
                          fitz.Point((ax + 27) * MM, (y + 6) * MM),
                          fitz.Point((ax + 20) * MM, (y + 8.6) * MM)],
                         color=None, fill=BLACK)

    s.line(x, y + 13, x + w, y + 13, 0.4)
    fields = ['DATE', 'FOIL', 'PAPER', 'TEMP °C', 'SPEED', 'PASSES']
    fx = x
    for f in fields:
        s.text(fx, y + 18, f, size=6, font=BOLD)
        s.line(fx + 14, y + 18, fx + 29, y + 18, 0.2)
        fx += w / len(fields)
    return 20


FOOTER_LINES = [
    'A / B   Thinnest width whose foil is unbroken end to end, in ALL THREE directions = your minimum stroke.',
    '        Vertical usually fails before horizontal. Take the worst, put it in MIN_STROKE_MM in foil_prep.py.',
    'C / D   Smallest type still readable with its serifs intact = your minimum point size. Reversed fails larger',
    '        than positive, so quote jobs from block D, not block C.',
    'E       Look across the 46mm flat at a low angle. Mottle here is why large fills get outlined instead.',
    'F       Smallest gap still open, not welded shut = your minimum clearance between strokes.',
    'G       Smallest dot fully foiled, and smallest hole not filled in. This is what limits counters in small type.',
    'H       Finest ruling that foils evenly, without speckle = your ceiling for any tonal artwork.',
]


def block_footer(s: Sheet, x, y, w) -> float:
    s.line(x, y, x + w, y, 0.3)
    s.text(x, y + 4, 'HOW TO READ IT', size=8, font=BOLD)
    ty = y + 8
    for line in FOOTER_LINES:
        s.text(x, ty, line, size=6)
        ty += 3.0
    return ty - y


# ---------------------------------------------------------------------------
# Sheet assembly
# ---------------------------------------------------------------------------

def build(out_path: str = 'foil_calibration.pdf', dpi: int = 600,
          lpi_list: list[int] | None = None) -> str:
    """Stack the blocks down one A4 sheet. Returns the output path."""
    lpi_list = lpi_list or HALFTONE_LPI
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W_MM * MM, height=PAGE_H_MM * MM)
    s = Sheet(page)

    x = MARGIN_MM
    w = PAGE_W_MM - 2 * MARGIN_MM
    col = (w - 4) / 2
    right = x + col + 4

    y = MARGIN_MM
    y += block_header(s, x, y, w) + BLOCK_GAP_MM
    y += max(block_lines(s, x, y, col),
             block_reversed_lines(s, right, y, col)) + BLOCK_GAP_MM
    y += max(block_text(s, x, y, col, reverse=False),
             block_text(s, right, y, col, reverse=True)) + BLOCK_GAP_MM
    y += max(block_solids(s, x, y, col),
             block_gaps(s, right, y, col)) + BLOCK_GAP_MM
    y += block_dots(s, x, y, w) + BLOCK_GAP_MM
    y += block_halftones(s, x, y, w, lpi_list, dpi) + BLOCK_GAP_MM
    y += block_footer(s, x, y, w)

    # Fail loud rather than ship a sheet with a block walked off the page.
    if y > PAGE_H_MM - 4:
        raise ValueError(
            f'Calibration sheet overflows A4: content ends at {y:.1f}mm of '
            f'{PAGE_H_MM}mm. Drop a test size or a screen ruling.')

    doc.save(out_path, garbage=4, deflate=True)
    doc.close()

    print(f'Saved: {out_path}  ({os.path.getsize(out_path) / 1e6:.2f} MB)')
    print(f'Content ends at {y:.0f}mm of {PAGE_H_MM}mm.')
    print(f'Halftones at {dpi} dpi, rulings: {", ".join(str(v) for v in lpi_list)} lpi')
    print('\nPrint it on the Konica: heavy stock, slowest speed, density max,')
    print('toner save OFF, simplex, 100% size (no "fit to page"). Then foil it')
    print('and fill in the boxes at the top.')
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Generate the TTF foil calibration sheet.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('output', nargs='?', default='foil_calibration.pdf',
                        help='Output PDF path')
    parser.add_argument('--dpi', type=int, default=600,
                        help='Halftone patch resolution; match the Konica engine')
    parser.add_argument('--lpi', default=','.join(str(v) for v in HALFTONE_LPI),
                        help='Comma-separated screen rulings to test (4 fit the row)')

    args = parser.parse_args()
    build(args.output, dpi=args.dpi,
          lpi_list=[int(v) for v in args.lpi.split(',') if v.strip()])


if __name__ == '__main__':
    main()
