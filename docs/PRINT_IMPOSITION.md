# Print imposition — the rules, and why they are the rules

_Last verified on paper: 2026-08-16, Konica bizhub PRO 1100 (OSP)._

Everything about how a sheet is laid out lives here. Read this before changing
`nup_imposer.py`, `print_planner.py`, or the print path in `print_server.py`.

This took six rounds and a lot of wasted paper to get right. Most of that was
spent on a model that could not have worked. The section "Do not reintroduce"
exists so nobody walks back into it.

---

## The one idea

**The printer's duplex unit plus the reader's flip is a rigid motion of the
sheet.** The only rigid motions that map a rectangle onto itself are 0° and
180°, and ink on paper cannot be mirrored.

So whatever any printer does to the back side, the residual error we could ever
need to correct is **exactly one bit**: does the back need turning 180°, or not?

That is the whole model. Everything below follows from it.

---

## The rules

### 1. Portrait canvas — every imposed sheet is portrait

A layout that logically wants landscape is composed **transposed** (cols and
rows swapped). The ink on the paper is the same; the PDF page handed to the
driver is portrait, where "long edge" is unambiguously the vertical one.

```
2-up  1x2 portrait      4-up  2x2 portrait
6-up  2x3 portrait      9-up  3x3 portrait
```

`print_planner.plan_print_job`, the `nup_map`. The JS mirror is `NUP_GRID` in
`website/order/order-logic.js` — **keep them in step** or the quoted sheet count
and the print-area warning stop matching what prints.

### 2. A portrait page is never rotated

Owner's rule. A portrait document must read without turning the sheet, even
where turning it would fill the slot better. On 2-up that means ~47% scale with
white bands either side — the ordinary portrait handout look.

A **landscape** source still turns to fit a portrait slot; left upright it would
be squeezed smaller and be harder to read, so the objection does not apply.

`nup_imposer.should_rotate_into_slot()`, mirrored as `shouldRotateIntoSlot()`
in the JS.

### 3. The printer is told one thing, always

```
paper=<size>, duplexlong
```

No orientation flag. No short-edge bind. No N-up token (SumatraPDF has none —
see below). Same string for every layout, every printer, both code paths.

### 4. One calibration constant, the same for every printer

`store_config.duplex_back_rotation` — `0` or `180`. Applied as a **true rigid
turn** of each back sheet-side: both slot axes reverse *and* the content turns
with them, so it can never degrade into a mirror.

Currently **180**, measured on the OSP Konica.

To calibrate a printer — two sheets, one duplex print, no code:

```
python tools/nup_doctor.py --calibrate
```

Whichever sheet reads correctly names the value. Set it in `store_config.json`
and restart.

---

## Do not reintroduce

**`duplex_mirror_axis()` / `binding_edge`.** Reversed slot *columns* without
rotating the content. That is a mirror, not a rigid motion, so it could never
have been the correct correction for a physical flip. Three rounds of reasoning
about long edge versus short edge were built on it and every one printed wrong.
`tests/test_nup_duplex_geometry.py::test_binding_edge_model_is_removed` guards
this.

**`nup2` / `nup4` in `-print-settings`.** SumatraPDF has no N-up token; it
discards them silently. Emitting them is how the staff print path looked like it
did N-up for months while doing nothing at all.

**Forcing `duplexshort` for landscape sheets.** Short-edge mode is long-edge plus
a 180° back rotation. On a 2×1 sheet that rotation also swaps the columns, so it
cancelled the imposer's own column mirroring — right page order, back printed
upside down. Two wrongs that looked like a right.

---

## Both print paths go through the planner

| Path | Entry | Used by |
|---|---|---|
| Jobs console | `print_server.handle_print_item` (`POST /print`) | staff |
| Paid web order | `store_puller.auto_print` | customers |

Both call `print_planner.plan_print_job`. There is **one** imposition
implementation. Until 2026-08-16 the console path had none, which is why fixing
the imposer changed nothing at the counter for several rounds.

---

## The doctor — use it before any paper

```
python tools/nup_doctor.py --nup 2        # what this PC would print
python tools/nup_doctor.py --nup 4
python tools/nup_doctor.py --calibrate    # A/B a new printer
python tools/nup_doctor.py --job OSKY-...  # a real job row
```

It reports the branch, the commit, whether the deployed planner has the current
rules, the resolved spec, the `sides` token going to the printer, and the page
order and rotation on every sheet-side — then saves the imposed PDF.

Expected for 2-up on 8 pages, `duplex_back_rotation = 180`:

```
imposed : 4 sheet-sides, 595x842pt (PORTRAIT)
  side 1 [FRONT]: 1@0deg   2@0deg
  side 2 [BACK ]: 4@180deg 3@180deg
```

**If the doctor is right and the paper is wrong, it is the printer** — run
`--calibrate`. That separation is the point of the tool.

---

## Verified on paper

| Layout | Status |
|---|---|
| 2-up duplex, B&W | ✅ 2026-08-16 |
| 2-up duplex, colour | ✅ 2026-08-16 |
| 4-up / 6-up / 9-up duplex | ⏳ never printed |
| Mixed colour + duplex | ⏳ never printed — splits across **both** printers, the one case where a single global constant could bite |
| Scale modes (actual/shrink/custom) | ⏳ never printed |
| A3 / A5 with N-up | ⏳ never printed |

Note: the 2-up result predates rule 2 (no rotating portraits), which changed
2-up fronts from 270° to 0°. Backs still sit exactly 180° from the fronts, so
the calibration carries over — but 2-up is worth one more sheet to confirm.

---

## Process rules, learned expensively

1. **Check the branch, not just the pull.** On 2026-08-16 the store PC sat on
   `feat/emc8100-migration` (tracking `origin/main`) while fixes went to a
   feature branch. `git pull` fetched them and said "Already up to date". Three
   rounds of print tests ran against stale code. Always confirm with
   `git log --oneline -1`.
2. **A checkout is not a deploy.** Python holds `print_planner` and
   `nup_imposer` in memory — `STOP_PRINTOSKY.bat` then `START_PRINTOSKY.bat`.
3. **Run the doctor before printing.** A one-sentence description of a sheet is
   almost no information; the doctor output is exact.
4. **Trace the code path the user's finger actually takes.** The console and the
   web order were different paths for months.
