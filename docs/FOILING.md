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

`MIN_STROKE_MM` in `tools/foil_prep.py` is a starting point, not gospel. Print
one sheet on the Konica and foil it:

- lines at 0.15 / 0.2 / 0.3 / 0.5 / 0.75 / 1.0 mm
- text at 6 / 8 / 10 / 12 / 16 / 24 pt, one serif and one sans
- solid squares at 10 / 25 / 50 mm
- halftone patches at 35 / 45 / 55 / 85 lpi
- reversed (knockout) text at the same sizes

Whatever survives *is* the spec for your Konica, your foil and your laminator.
Update the constant to match and stop guessing.
