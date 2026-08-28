"""
foil_prep.py — Convert artwork to a foil-ready file for TTF (toner-transfer foil).

Usage:
    python foil_prep.py cover.pdf                        # -> cover_FOIL.pdf
    python foil_prep.py cover.pdf --threshold 60 --thicken 0.10
    python foil_prep.py cover.pdf --variants             # A/B/C to test on paper
    python foil_prep.py cover.pdf --report               # dropout risk only

Why this exists:
    Foil bonds to toner mass, not to darkness. A 40% grey renders as a field of
    isolated halftone dots — the foil grabs some and misses others, and the job
    comes out speckled. So the whole conversion is: make every mark either solid
    toner or bare paper, at 600 dpi, with nothing thinner than the foil can hold.

    Toner only, so the Konica. Foil will not stick to Epson inkjet output.
"""

import argparse
import io
import os
import sys

import fitz
import numpy as np
from PIL import Image, ImageFilter, ImageOps

# Below this stroke width foil starts skipping. A starting point, not gospel —
# the real numbers are whatever survives the calibration sheet on your own
# laminator and foil. See docs/FOILING.md.
MIN_STROKE_MM = 0.30
RISKY_STROKE_MM = 0.45


# ---------------------------------------------------------------------------
# Core image processing
# ---------------------------------------------------------------------------

def binarise(img: Image.Image, threshold: int, smooth_px: float = 0.0) -> Image.Image:
    """
    Grey -> pure 1-bit, by global threshold.

    Deliberately global, not adaptive: adaptive thresholding is tuned for
    scanned text and invents noise in the large flat areas of a cover, which is
    exactly what must not happen under foil. Deliberately not dithered either —
    dithering is the worst possible foil input.

    threshold  Cut point [0-100]. Higher keeps more ink (heavier foil).
    smooth_px  Blur radius before the cut. Counter-intuitive but measured: on a
               low-resolution source, sharpening the edge makes the threshold
               land on the source's own pixel staircase and the letters come out
               ragged and broken. Blurring first lets the cut find a smooth
               contour through the upscaled edge. Sized from the upscale factor
               by auto_smooth().
    """
    img = ImageOps.autocontrast(img, cutoff=1)
    if smooth_px > 0:
        img = img.filter(ImageFilter.GaussianBlur(smooth_px))
    cut = int(255 * threshold / 100)
    arr = np.asarray(img)
    return Image.fromarray(np.where(arr < cut, 0, 255).astype(np.uint8), 'L')


def source_dpi(page) -> float | None:
    """
    Effective resolution of the artwork on the page, or None if it is vector.

    A cover handed over as a flattened export carries one big image; its pixel
    count against the page size is the real resolution of the job, whatever the
    PDF claims. That number decides how much smoothing the threshold needs.
    """
    images = page.get_images(full=True)
    if not images:
        return None
    widest = max(images, key=lambda x: x[2])
    return widest[2] / (page.rect.width / 72)


def auto_smooth(page, dpi: int) -> float:
    """Blur radius for this page: 0.65 x the upscale factor, 0.8px for vector."""
    src = source_dpi(page)
    if src is None or src >= dpi:
        return 0.8
    return round(0.65 * (dpi / src), 2)


def close_pinholes(img: Image.Image, radius_px: int = 1) -> Image.Image:
    """Fill pinholes inside solids (dilate black, then erode). Pinhole = foil void."""
    if radius_px < 1:
        return img
    size = 2 * radius_px + 1
    return img.filter(ImageFilter.MinFilter(size)).filter(ImageFilter.MaxFilter(size))


def thicken(img: Image.Image, radius_px: int) -> Image.Image:
    """Grow every black mark by radius_px on each side. Rescues thin strokes."""
    if radius_px < 1:
        return img
    return img.filter(ImageFilter.MinFilter(2 * radius_px + 1))


def mm_to_px(mm: float, dpi: int) -> int:
    return int(round(mm / 25.4 * dpi))


# ---------------------------------------------------------------------------
# Stroke-width measurement
# ---------------------------------------------------------------------------

def _run_lengths(mask: np.ndarray) -> np.ndarray:
    """
    Per-pixel run length along axis 1, vectorised over the whole array.

    Rows are joined with a False sentinel so runs cannot bleed between them.
    """
    h, w = mask.shape
    flat = np.concatenate([mask, np.zeros((h, 1), dtype=bool)], axis=1).ravel()
    edges = np.flatnonzero(np.diff(np.concatenate(([False], flat, [False])).view(np.int8)))
    starts, ends = edges[::2], edges[1::2]
    out = np.zeros(flat.size, dtype=np.uint16)
    if starts.size:
        lengths = np.minimum(ends - starts, 65535).astype(np.uint16)
        out[starts] = lengths
        # Forward-fill each run with its own length.
        idx = np.arange(flat.size)
        marker = np.where(out > 0, idx, 0)
        np.maximum.accumulate(marker, out=marker)
        out = np.where(flat, out[marker], 0)
    return out.reshape(h, w + 1)[:, :w]


def stroke_widths(bw: Image.Image) -> np.ndarray:
    """
    Approximate stroke width at every ink pixel, in pixels.

    width = min(horizontal run, vertical run) through that pixel — cheap, and
    close enough to a distance transform for deciding what foil will hold.
    """
    mask = np.asarray(bw) < 128
    h_run = _run_lengths(mask)
    v_run = _run_lengths(mask.T).T
    widths = np.minimum(h_run, v_run)
    return widths[mask]


def risk_report(bw: Image.Image, dpi: int) -> dict:
    """Share of ink that sits in strokes too thin for foil to hold."""
    widths_px = stroke_widths(bw)
    if widths_px.size == 0:
        return {'ink_pct': 0.0, 'lost_pct': 0.0, 'risky_pct': 0.0, 'median_mm': 0.0}
    widths_mm = widths_px.astype(np.float32) / dpi * 25.4
    total = float(np.asarray(bw).size)
    return {
        'ink_pct':   100.0 * widths_px.size / total,
        'lost_pct':  100.0 * float((widths_mm < MIN_STROKE_MM).sum()) / widths_px.size,
        'risky_pct': 100.0 * float((widths_mm < RISKY_STROKE_MM).sum()) / widths_px.size,
        'median_mm': float(np.median(widths_mm)),
    }


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def make_output_path(src: str, dst: str | None, suffix: str = '_FOIL') -> str:
    if dst:
        return dst
    root, ext = os.path.splitext(src)
    return root + suffix + ext


def convert(
    src: str,
    dst: str | None = None,
    dpi: int = 600,
    threshold: int = 55,
    thicken_mm: float = 0.0,
    close_mm: float = 0.05,
    smooth: float | None = None,
    suffix: str = '_FOIL',
    report: bool = True,
    preview: bool = False,
) -> str:
    """
    Render a PDF to foil-ready 1-bit pages. Returns the output path.

    dpi         Output resolution. 600 matches the Konica engine; do not go lower.
    threshold   Ink cut point [0-100]. The one knob worth turning.
    thicken_mm  Grow every mark by this much per side. 0.05-0.10 rescues thin serifs.
    close_mm    Pinhole fill radius inside solids.
    smooth      Pre-threshold blur in pixels. None -> sized from the source
                resolution, which is the right answer nearly always.
    """
    if not os.path.exists(src):
        raise FileNotFoundError(f'Input not found: {src}')

    out_path = make_output_path(src, dst, suffix)
    doc = fitz.open(src)
    out = fitz.open()
    mat = fitz.Matrix(dpi / 72, dpi / 72)

    print(f'Input     : {os.path.basename(src)}  ({len(doc)} page(s))')
    print(f'Output    : {os.path.basename(out_path)}')
    print(f'{dpi} dpi | threshold {threshold}% | thicken {thicken_mm}mm | close {close_mm}mm')
    print()

    for i, page in enumerate(doc):
        rect = page.rect
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        img = Image.frombytes('L', (pix.width, pix.height), pix.samples)

        smooth_px = auto_smooth(page, dpi) if smooth is None else smooth
        bw = binarise(img, threshold, smooth_px)
        bw = close_pinholes(bw, mm_to_px(close_mm, dpi))
        bw = thicken(bw, mm_to_px(thicken_mm, dpi))

        if report:
            src = source_dpi(page)
            origin = f'{src:.0f} dpi raster' if src else 'vector'
            r = risk_report(bw, dpi)
            flag = '  <-- CHECK' if r['lost_pct'] > 2.0 else ''
            print(f"  p{i + 1}: source {origin} | smooth {smooth_px}px")
            print(f"      ink {r['ink_pct']:.2f}% of sheet | median stroke "
                  f"{r['median_mm']:.2f}mm | below {MIN_STROKE_MM}mm: {r['lost_pct']:.1f}% of ink"
                  f" | below {RISKY_STROKE_MM}mm: {r['risky_pct']:.1f}%{flag}")

        if preview:
            bw.save(f'{os.path.splitext(out_path)[0]}_p{i + 1}.png', optimize=True)

        buf = io.BytesIO()
        bw.convert('1').save(buf, 'PNG', optimize=True)
        new_page = out.new_page(width=rect.width, height=rect.height)
        new_page.insert_image(rect, stream=buf.getvalue())

    out.save(out_path, garbage=4, deflate=True)
    out.close()
    doc.close()

    print(f'\nSaved: {out_path}  ({os.path.getsize(out_path) / 1e6:.1f} MB)')
    return out_path


VARIANTS = [
    ('_FOIL_A_fine',     45, 0.00),
    ('_FOIL_B_balanced', 55, 0.06),
    ('_FOIL_C_heavy',    65, 0.12),
]


def convert_variants(src: str, **kw) -> list[str]:
    """Three tuned versions to foil side by side and pick from, on paper."""
    paths = []
    for suffix, threshold, thicken_amount in VARIANTS:
        print(f'--- {suffix.strip("_")} ---')
        paths.append(convert(src, dst=None, threshold=threshold,
                             thicken_mm=thicken_amount, suffix=suffix, **kw))
        print()
    return paths


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Convert artwork to a foil-ready 1-bit file for TTF foiling.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('input', help='Source PDF path')
    parser.add_argument('output', nargs='?', default=None,
                        help='Output path (default: <input>_FOIL.pdf)')
    parser.add_argument('--dpi', type=int, default=600,
                        help='Output resolution; 600 matches the Konica engine')
    parser.add_argument('--threshold', type=int, default=55,
                        help='Ink cut point [0-100]. Higher = heavier foil coverage.')
    parser.add_argument('--thicken', type=float, default=0.0, dest='thicken_mm',
                        help='Grow every mark by this many mm per side')
    parser.add_argument('--close', type=float, default=0.05, dest='close_mm',
                        help='Pinhole fill radius in mm')
    parser.add_argument('--smooth', type=float, default=None,
                        help='Pre-threshold blur in px (default: sized from source resolution)')
    parser.add_argument('--variants', action='store_true',
                        help='Emit fine / balanced / heavy versions to test on paper')
    parser.add_argument('--preview', action='store_true',
                        help='Also write a PNG of each converted page')
    parser.add_argument('--no-report', action='store_true',
                        help='Skip the thin-stroke dropout report')

    args = parser.parse_args()
    common = dict(dpi=args.dpi, close_mm=args.close_mm, smooth=args.smooth,
                  report=not args.no_report, preview=args.preview)

    try:
        if args.variants:
            convert_variants(args.input, **common)
        else:
            convert(args.input, dst=args.output, threshold=args.threshold,
                    thicken_mm=args.thicken_mm, **common)
    except FileNotFoundError as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
