# Printosky logo — locked 2026-06-13

The mark is the **Gabarito ExtraBold capital P** with its bowl knocked out and the **Oxygen O** (the parent‑brand teal ring + pupil) sitting in the bowl. The Oxygen lineage appears exactly once, in the P. The glyph is **outlined to vector paths** — **no font needed to render** — and the ring's counter/knockout are **truly transparent**, so one file sits on any background.

## Colours

| Role | Hex |
|------|-----|
| Printosky orange (primary) | `#F0571E` |
| Oxygen teal (ring) | `#1597C4` |
| Navy (pupil, on light) | `#182A3D` |
| Ink (dark backgrounds) | `#17130F` |
| Cream (light backgrounds) | `#FAF7F2` |

On dark backgrounds the pupil flips to white — use the `-reverse` files.

## Files

| File | Use |
|------|-----|
| `printosky-icon.svg` | App icon / standalone P — light backgrounds |
| `printosky-icon-reverse.svg` | Standalone P — dark backgrounds (white pupil) |
| `printosky-wordmark.svg` | Full "Printosky" lockup — light backgrounds |
| `printosky-wordmark-reverse.svg` | Full lockup — dark backgrounds |
| `printosky-icon.png` / `-reverse.png` | Icon PNG, 1024×1024 transparent (light / dark) |
| `printosky-wordmark.png` / `-reverse.png` | Wordmark PNG, 2048×608 transparent (light / dark) |
| `favicon.ico` | Multi‑res (16/32/48) browser favicon |
| `favicon-16/32/48.png` | Transparent PNG favicons |
| `icon-512.png` | 512px transparent (PWA / social / general) |
| `apple-touch-180.png` | Apple touch icon (white background, 180px) |

The counter of the ring is **truly transparent**, so a single icon file sits on any background colour.

## Web wiring (oxygens-website)

```html
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
<link rel="apple-touch-icon" href="/apple-touch-180.png">
```

## Regenerating

Self-contained in this folder: run `python build_logo.py` (needs `fonttools`; `Gabarito.ttf` ships here, instanced to wght=800 at build time). It outlines the glyph(s), masks out the bowl, and overlays the Oxygen ring. Ring geometry (in the size‑120 P space): centre `(48, 31)`, knockout `r33`, teal annulus `r27/r18`, pupil `r9`. Re‑render PNGs with `@resvg/resvg-js`.

## Typography

Wordmark / brand font: **Gabarito ExtraBold (800)** — OFL, Google Fonts.
