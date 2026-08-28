# TTF foiling — preparing artwork

Toner-transfer foil bonds to **toner mass**, not to darkness. Every rule below
follows from that one fact.

## The hardware constraint

Foil must be printed on the **Konica Bizhub Pro 1100** (B&W toner, `OSP`).

**The Epson EM-C8100 is inkjet — foil will not stick to it at all.** Nattika
(`PRINTK`) has no Konica, so foil work there has to be printed at Thriprayar
first. Quote it accordingly.

## What the file has to look like

A 40% grey looks dark on paper but prints as a field of isolated halftone dots.
Foil grabs some and misses others, and the sheet comes out speckled. So:

**Every mark is either solid toner or bare paper. No greys, no halftones, no
anti-aliasing, no gradients, no dithering.**

| Rule | Why |
|---|---|
| Pure 1-bit black/white, global threshold | Grey → halftone dots → speckle |
| Never dither (no Floyd–Steinberg) | Dithering is the worst possible foil input |
| Minimum stroke **0.30 mm**, safe at 0.45 mm | Thinner and the foil skips |
| Minimum text ≈ 10 pt; avoid hairline serifs | Serif tips drop out first |
| Build at final size, 600 dpi, *then* threshold | Threshold-then-scale re-introduces grey |
| 100% K, toner-save off, density max | CMY under foil weakens the bond |
| No mirroring | Foil goes on top of toner, unlike PCB toner transfer |

## Doing it

```bash
python tools/foil_prep.py cover.pdf              # -> cover_FOIL.pdf, plus a risk report
python tools/foil_prep.py cover.pdf --variants   # fine / balanced / heavy, to test on paper
python tools/foil_prep.py cover.pdf --threshold 60 --thicken 0.10
```

`--threshold` is the one knob worth turning: higher keeps more ink and gives
heavier foil coverage. `--thicken` grows every mark by that many mm per side and
is what rescues thin serifs and small caps.

The report after each page is the useful part:

```
p1: source 128 dpi raster | smooth 3.06px
    ink 2.97% of sheet | median stroke 0.80mm | below 0.3mm: 1.4% of ink | below 0.45mm: 7.0%
```

`below 0.3mm` over about 2% earns a `<-- CHECK`: that share of the artwork is
likely to skip. Raise `--thicken` and run it again.

### Sources handed over as flattened exports

Most covers arrive as a Word or LaTeX export flattened to one image — often
around 130 dpi, with no live text at all. `foil_prep` reports the real source
resolution so you can see this before printing.

Such a file needs **smoothing before the threshold, never sharpening.** Sharpening
makes the cut land on the source's own pixel staircase and the letters come out
ragged and broken; blurring first lets it find a smooth contour through the
upscaled edge. The tool sizes this from the upscale factor automatically.

Better still, ask the student for the original document and export it as vector
PDF. Vector never produces a grey pixel at all, and it is always worth one
WhatsApp message before a hardbound cover.

## Printing and foiling

- Paper: **smooth or coated stock**. Textured or laid paper foils patchily
  however good the file is.
- Konica: heavy/thick-stock mode, slowest speed, toner density max, toner save
  off, simplex.
- Laminator: foil shiny side **up**, roughly 110–130 °C, slow, **two passes**
  (the second rotated 90° fixes most streaking). Peel cool for fine detail, warm
  for large solids.
- Large solid fills are the hardest case — expect some mottle. An outline with an
  inner pattern foils far more reliably than a full block.

## Calibrate once

`MIN_STROKE_MM` in `tools/foil_prep.py` is a starting point borrowed from other
people's machines. Replace it with yours:

```bash
python tools/foil_calibration.py       # -> foil_calibration.pdf
```

Print that on the Konica — heavy stock, slowest speed, density max, toner save
off, simplex, **100% size, no "fit to page"** — then foil it exactly the way you
foil real work, and fill in the boxes at the top (date, foil, paper, temperature,
speed, passes).

Eight blocks, each settling one number:

| | Block | What it tells you |
|---|---|---|
| A | Positive lines, 0.10–1.00 mm, horizontal / vertical / 45° | Minimum stroke. Vertical usually fails first — take the worst direction |
| B | The same widths knocked out of a solid | Minimum reversed stroke; always worse than positive |
| C | Type 6–16 pt, serif and sans | Minimum point size |
| D | The same type reversed | Minimum reversed point size — quote from this one |
| E | 46 / 25 / 10 mm solids | Where flats go mottled |
| F | Gaps 0.10–0.50 mm between bars and between solids | Minimum clearance before foil bridges strokes shut |
| G | Dots 0.20–1.50 mm, positive and knocked out | What limits counters in small type |
| H | 35 / 45 / 55 / 85 lpi at 25 / 50 / 75% | Finest screen ruling that foils without speckle |

Whatever survives *is* the spec for your Konica, your foil and your laminator.
Write the numbers into this file and into `MIN_STROKE_MM`, and stop guessing.

Re-run it per foil type, and again whenever the laminator is serviced or
replaced.

The strokes and type on the sheet are vector so their widths are exact — a
0.15 mm line rasterised at 600 dpi is 15% off, which is the difference between a
pass and a fail on the row that decides your minimum. Only the halftone patches
are raster, at 600 dpi, so that the screen ruling is ours and not whatever the
RIP would have picked. Each patch is cut at its tone's quantile, so a patch
labelled 50% carries 50% ink.
