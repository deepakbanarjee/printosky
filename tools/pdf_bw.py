"""
pdf_bw.py — Convert any scanned book/document PDF to clean B&W for printing.

Usage:
    python pdf_bw.py input.pdf
    python pdf_bw.py input.pdf output.pdf
    python pdf_bw.py input.pdf --dpi 200 --sensitivity 0.10

Algorithm:
    - Text pages  → Bradley adaptive threshold (pure black/white, no grey)
    - Image pages → Background normalisation (preserves tones, forces paper → white)

Page type is detected automatically from the mid-tone pixel ratio of each page.
"""

import argparse
import io
import os
import sys
import time

import fitz
import numpy as np
from PIL import Image, ImageFilter, ImageOps


# ---------------------------------------------------------------------------
# Core image processing
# ---------------------------------------------------------------------------

def bradley_threshold(arr: np.ndarray, window: int = 40, sensitivity: float = 0.10) -> np.ndarray:
    """
    Bradley's adaptive local threshold via integral images.

    Each pixel is declared ink when its value falls below
    (1 - sensitivity) × local_window_mean.  Higher sensitivity → more
    aggressive (picks up lighter ink, but also more paper noise).

    Uses np.ix_ broadcasting instead of meshgrid — ~4x less peak memory.
    """
    h, w = arr.shape
    s = window // 2
    f = arr.astype(np.float64)

    integral = np.zeros((h + 1, w + 1), dtype=np.float64)
    integral[1:, 1:] = np.cumsum(np.cumsum(f, axis=0), axis=1)

    rows = np.arange(h)
    cols = np.arange(w)
    r1 = np.maximum(rows - s, 0)
    r2 = np.minimum(rows + s, h - 1)
    c1 = np.maximum(cols - s, 0)
    c2 = np.minimum(cols + s, w - 1)

    count = np.outer(r2 - r1 + 1, c2 - c1 + 1)
    window_sum = (
        integral[np.ix_(r2 + 1, c2 + 1)]
        - integral[np.ix_(r1,     c2 + 1)]
        - integral[np.ix_(r2 + 1, c1    )]
        + integral[np.ix_(r1,     c1    )]
    )

    threshold = (window_sum / count) * (1.0 - sensitivity)
    return np.where(f < threshold, 0, 255).astype(np.uint8)


def normalise_image_background(img: Image.Image, blur_radius: int = 60, white_point: int = 220) -> Image.Image:
    """
    For pages with photos/illustrations: divide each pixel by its local
    background estimate (large BoxBlur), forcing paper → white while
    preserving illustration mid-tones.  Pixels above white_point are
    hard-clipped to 255.
    """
    arr = np.array(img, dtype=np.float32)
    bg  = np.array(img.filter(ImageFilter.BoxBlur(blur_radius)), dtype=np.float32)
    normalised = np.clip((arr / (bg + 1.0)) * 255.0, 0, 255)
    normalised[normalised > white_point] = 255
    return Image.fromarray(normalised.astype(np.uint8), 'L')


def is_image_page(img: Image.Image, tone_split: float = 0.30) -> bool:
    """
    True when the page contains significant continuous-tone content
    (photos, shaded diagrams).  Detected via mid-tone pixel ratio.

    Calibrated against scan samples:
        text pages  -> 20-23% mid-tone
        image pages -> 38-54% mid-tone
    """
    hist  = img.histogram()
    total = sum(hist)
    if total == 0:
        return False
    return sum(hist[40:215]) / total > tone_split


# ---------------------------------------------------------------------------
# Output path helpers
# ---------------------------------------------------------------------------

def make_output_path(src: str, dst: str | None) -> str:
    """Return a safe output path, auto-versioning if the file is open/locked."""
    if dst:
        base = dst
    else:
        root, ext = os.path.splitext(src)
        base = root + '_BW' + ext

    path = base
    root, ext = os.path.splitext(base)
    v = 2
    while _is_locked(path):
        path = f'{root}_v{v}{ext}'
        v += 1
    return path


def _is_locked(path: str) -> bool:
    if not os.path.exists(path):
        return False
    try:
        os.rename(path, path)
        return False
    except OSError:
        return True


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------

def convert(
    src: str,
    dst: str | None = None,
    dpi: int = 200,
    sensitivity: float = 0.10,
    window: int = 40,
    tone_split: float = 0.30,
    white_point: int = 220,
) -> str:
    """
    Convert a scanned PDF to clean B&W.  Returns the output path.

    Args:
        src          Path to source PDF.
        dst          Output path (None -> auto-name as <src>_BW.pdf).
        dpi          Render DPI. 200 is a good balance for book scans.
        sensitivity  Bradley threshold sensitivity [0-1]. Raise if thin
                     strokes disappear; lower if paper noise shows as ink.
        window       Bradley local window size in pixels.
        tone_split   Mid-tone ratio above which a page is treated as image
                     rather than text [0-1].
        white_point  Image-page pixels above this are clipped to white [0-255].
    """
    if not os.path.exists(src):
        raise FileNotFoundError(f'Input not found: {src}')

    out_path = make_output_path(src, dst)
    doc  = fitz.open(src)
    out  = fitz.open()
    zoom = dpi / 72
    mat  = fitz.Matrix(zoom, zoom)

    n_pages     = len(doc)
    text_pages  = 0
    image_pages = 0
    t0          = time.time()

    print(f'Input  : {os.path.basename(src)}  ({os.path.getsize(src)/1e6:.1f} MB, {n_pages} pages)')
    print(f'Output : {os.path.basename(out_path)}')
    print(f'DPI {dpi}  |  sensitivity {sensitivity}  |  window {window}px')
    print()

    for i, page in enumerate(doc):
        rect = page.rect
        pix  = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        img  = Image.frombytes('L', (pix.width, pix.height), pix.samples)

        # Stretch histogram: paper -> near 255, ink -> near 0
        img = ImageOps.autocontrast(img, cutoff=2)

        buf = io.BytesIO()

        if is_image_page(img, tone_split):
            img = normalise_image_background(img, white_point=white_point)
            img.save(buf, 'JPEG', quality=85, optimize=True)
            image_pages += 1
        else:
            img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
            arr = np.array(img)
            Image.fromarray(
                bradley_threshold(arr, window=window, sensitivity=sensitivity), 'L'
            ).save(buf, 'PNG', optimize=True)
            text_pages += 1

        new_page = out.new_page(width=rect.width, height=rect.height)
        new_page.insert_image(rect, stream=buf.getvalue())

        # Inline progress bar with ETA
        done    = i + 1
        elapsed = time.time() - t0
        eta     = (elapsed / done) * (n_pages - done)
        filled  = int(30 * done / n_pages)
        bar     = '#' * filled + '.' * (30 - filled)
        print(f'\r  [{bar}] {done}/{n_pages}  ETA {eta:.0f}s  ', end='', flush=True)

    print()

    out.save(out_path, garbage=4, deflate=True)
    out.close()
    doc.close()

    elapsed = time.time() - t0
    print(f'\nText pages  (binarised) : {text_pages}')
    print(f'Image pages (greyscale) : {image_pages}')
    print(f'Size : {os.path.getsize(src)/1e6:.1f} MB -> {os.path.getsize(out_path)/1e6:.1f} MB')
    print(f'Time : {elapsed:.0f}s')
    print(f'Saved: {out_path}')

    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Convert a scanned book PDF to clean B&W for printing.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('input',  help='Source PDF path')
    parser.add_argument('output', nargs='?', default=None,
                        help='Output path  (default: <input>_BW.pdf)')
    parser.add_argument('--dpi',         type=int,   default=200,
                        help='Render resolution')
    parser.add_argument('--sensitivity', type=float, default=0.10,
                        help='Bradley threshold sensitivity [0-1]. '
                             'Raise to keep lighter strokes; lower to cut paper noise.')
    parser.add_argument('--window',      type=int,   default=40,
                        help='Bradley local window size (pixels)')
    parser.add_argument('--tone-split',  type=float, default=0.30,
                        help='Mid-tone ratio for image-page detection [0-1]')
    parser.add_argument('--white-point', type=int,   default=220,
                        help='Image-page background clip value [0-255]')

    args = parser.parse_args()

    try:
        convert(
            src         = args.input,
            dst         = args.output,
            dpi         = args.dpi,
            sensitivity = args.sensitivity,
            window      = args.window,
            tone_split  = args.tone_split,
            white_point = args.white_point,
        )
    except FileNotFoundError as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
