# Page rotation matrix — every combination

> **✅ RESOLVED — OSP Konica duplex/simplex control fixed via dual-queue routing, 2026-08-30.**
> The `KONICA MINOLTA 1100 PS` driver silently ignores SumatraPDF's per-job
> `duplex`/`simplex` override in both directions (full writeup under "Not yet
> on paper" below) — a driver limitation, never fixed at the SumatraPDF/code
> level. Worked around by installing the same physical Konica as two Windows
> queues, each with its own persisted Printing Preferences default, and
> routing by queue instead of by per-job override
> (`print_server._konica_queue_for_sides()`, `install/INSTALL.md`). Verified
> on paper at OSP: `tools/nup_final_test.py --simplex --send --printer konica`
> printed two genuinely separate single-sided sheets, and
> `--only 2up_landscape --send --printer konica` printed one genuine duplex
> sheet, back correctly turned. Epson re-verified the same way — both
> printers now confirmed correct for duplex and simplex control.

_Generated from the code. Regenerate with `python tools/nup_matrix.py --markdown`._

Every rotation the imposer can apply, for every combination of layout,
orientation choice and fill direction. The numbers here come out of
`nup_imposer.impose_plan` — the same function `perform_nup` draws from — so this
table **is** the imposer, not a description of it. `tests/test_nup_rotation_matrix.py`
holds it in place.

The longer write-up of how this model was arrived at — six rounds and a lot of
wasted paper — lives in `docs/PRINT_IMPOSITION.md` on branch
`claude/oxygen-store-tasks-ys0j66`, which is not merged. Worth reading before
changing anything here.

---

## Every imposed sheet is portrait

The whole document goes to the printer as **portrait duplex**, always. One
paper shape, one `duplexlong` token, no orientation flag. A layout that
logically wants landscape is composed **transposed** onto that portrait sheet —
cols and rows swapped — rather than handed to the driver as a landscape page.

So "portrait" and "landscape" throughout this document name the **layout the
customer chose**, never the paper. `test_every_imposed_sheet_is_portrait`
asserts it across all 10 layout/orientation combinations, and
`test_every_imposed_sheet_leaves_as_portrait` asserts it again at the planner
level, where it also checks no orientation flag reaches SumatraPDF.

## The rules

**1. Choosing landscape turns every page 90°, and turns the back side a further
180°.** So fronts sit at 90° and backs at 270° — which is −90°. 1-up and N-up
alike, one turn direction for the whole model.

**2. Portrait turns nothing.** Fronts and backs both at 0°, whatever the
layout.

**3. On a landscape N-up, page 1 lands at the BOTTOM.** The pages are laid out
in the logical landscape arrangement, and that whole arrangement is turned 90°
anticlockwise onto the portrait sheet. The landscape sheet's left edge becomes
the portrait sheet's bottom, so the reader turns the sheet clockwise to read
it and finds page 1 first:

```
landscape arrangement          on the portrait sheet
+-----+-----+                  +-----+-----+
|  1  |  2  |        -->       |  2  |  4  |
+-----+-----+                  +-----+-----+
|  3  |  4  |                  |  1  |  3  |
+-----+-----+                  +-----+-----+
```

`nup_imposer.slot_position` does the placement; `layout_rotation` the angle.

Notice the back is always exactly 180° from the front, or not turned at all.
That is not a coincidence — the duplex unit plus the reader's flip is a rigid
motion of the sheet, and the only rigid motions that map a rectangle onto
itself are 0° and 180°. Ink on paper cannot be mirrored.

## The 180° is a rigid turn, not a column swap

When the back turns 180°, **both slot axes reverse and the content turns with
them**:

```
2-up landscape front     back, turned a rigid 180
+-----------+            +-----------+
|  2 @ 90   |            |  3 @ 270  |
+-----------+            +-----------+
|  1 @ 90   |            |  4 @ 270  |
+-----------+            +-----------+
                         slots reversed AND content turned with them
```

Reversing one axis *without* turning the content is a mirror. That was the old
`eff_col`-only code, and it is why landscape 2-up duplex printed in the right
page order with the back upside down. `test_back_side_is_a_rigid_turn_not_a_mirror`
guards against it coming back.

## Direction changes order, never angle

`horizontal` fills row-major (left to right, then down). `vertical` fills
column-major (top to bottom, then across). Neither ever changes a rotation —
`test_direction_never_changes_a_rotation` asserts it across all 20 combinations.

---

## The matrix

Angles apply to a **portrait source page** — the normal case. A landscape
source turns 90° where its slot disagrees; see "Landscape sources" below. Cells
read `page@angle`, in physical slot order (left to right, top to bottom) on the
sheet as it prints.

| Layout | Orientation | Direction | Grid | Front side | Back side | Back turn |
|---|---|---|---|---|---|---|
| 1-up | portrait | horizontal | 1×1 | 1@0° | 2@0° | 0° |
| 1-up | portrait | vertical | 1×1 | 1@0° | 2@0° | 0° |
| 1-up | landscape | horizontal | 1×1 | 1@90° | 2@270° | 180° |
| 1-up | landscape | vertical | 1×1 | 1@90° | 2@270° | 180° |
| 2-up | portrait | horizontal | 1×2 | 1@0° 2@0° | 3@0° 4@0° | 0° |
| 2-up | portrait | vertical | 1×2 | 1@0° 2@0° | 3@0° 4@0° | 0° |
| 2-up | landscape | horizontal | 1×2 | 2@90° 1@90° | 3@270° 4@270° | 180° |
| 2-up | landscape | vertical | 1×2 | 2@90° 1@90° | 3@270° 4@270° | 180° |
| 4-up | portrait | horizontal | 2×2 | 1@0° 2@0° 3@0° 4@0° | 5@0° 6@0° 7@0° 8@0° | 0° |
| 4-up | portrait | vertical | 2×2 | 1@0° 3@0° 2@0° 4@0° | 5@0° 7@0° 6@0° 8@0° | 0° |
| 4-up | landscape | horizontal | 2×2 | 2@90° 4@90° 1@90° 3@90° | 7@270° 5@270° 8@270° 6@270° | 180° |
| 4-up | landscape | vertical | 2×2 | 3@90° 4@90° 1@90° 2@90° | 6@270° 5@270° 8@270° 7@270° | 180° |
| 6-up | portrait | horizontal | 2×3 | 1@0° 2@0° 3@0° 4@0° 5@0° 6@0° | 7@0° 8@0° 9@0° 10@0° 11@0° 12@0° | 0° |
| 6-up | portrait | vertical | 2×3 | 1@0° 4@0° 2@0° 5@0° 3@0° 6@0° | 7@0° 10@0° 8@0° 11@0° 9@0° 12@0° | 0° |
| 6-up | landscape | horizontal | 2×3 | 3@90° 6@90° 2@90° 5@90° 1@90° 4@90° | 10@270° 7@270° 11@270° 8@270° 12@270° 9@270° | 180° |
| 6-up | landscape | vertical | 2×3 | 5@90° 6@90° 3@90° 4@90° 1@90° 2@90° | 8@270° 7@270° 10@270° 9@270° 12@270° 11@270° | 180° |
| 9-up | portrait | horizontal | 3×3 | 1@0° 2@0° 3@0° 4@0° 5@0° 6@0° 7@0° 8@0° 9@0° | 10@0° 11@0° 12@0° 13@0° 14@0° 15@0° 16@0° 17@0° 18@0° | 0° |
| 9-up | portrait | vertical | 3×3 | 1@0° 4@0° 7@0° 2@0° 5@0° 8@0° 3@0° 6@0° 9@0° | 10@0° 13@0° 16@0° 11@0° 14@0° 17@0° 12@0° 15@0° 18@0° | 0° |
| 9-up | landscape | horizontal | 3×3 | 3@90° 6@90° 9@90° 2@90° 5@90° 8@90° 1@90° 4@90° 7@90° | 16@270° 13@270° 10@270° 17@270° 14@270° 11@270° 18@270° 15@270° 12@270° | 180° |
| 9-up | landscape | vertical | 3×3 | 7@90° 8@90° 9@90° 4@90° 5@90° 6@90° 1@90° 2@90° 3@90° | 12@270° 11@270° 10@270° 15@270° 14@270° 13@270° 18@270° 17@270° 16@270° | 180° |

Simplex is the front row of each combination, with no back side. Add
`--simplex` to the tool to print those too.

### Reading it as sheets

`python tools/nup_matrix.py` draws each combination in its physical slot
positions, which is easier to check against paper:

```
2-up  landscape horizontal duplex   portrait sheet, grid 1x2  back turn 180deg
  sheet 1 [FRONT]    2@90 
                     1@90 
  sheet 1 [BACK ]    3@270
                     4@270

6-up  landscape horizontal duplex   portrait sheet, grid 2x3  back turn 180deg
  sheet 1 [FRONT]    3@90    6@90 
                     2@90    5@90 
                     1@90    4@90 
  sheet 1 [BACK ]   10@270   7@270
                    11@270   8@270
                    12@270   9@270
```

---

## Grid shapes

`nup_imposer.SHEET_GRIDS`. One grid per layout — the orientation choice does
**not** change it, because transposing a landscape arrangement onto a portrait
sheet swaps its axes and lands back on the same shape.

| Layout | Grid on the portrait sheet |
|---|---|
| 1-up | 1×1 |
| 2-up | 1×2 (stacked) |
| 4-up | 2×2 |
| 6-up | 2×3 |
| 9-up | 3×3 |

On `auto` — or nothing — the orientation resolves to portrait. Nothing turns
unless someone asks for it.

## One turn direction, everywhere

There is no per-layout switch. Landscape always turns the content 90° and
always transposes the arrangement, 1-up and N-up alike. An earlier draft kept
N-up pages upright at 0°; that was rejected on the sample sheets — a landscape
selection means a landscape reading, so the pages turn and fill their slots.

### Landscape sources

On a PORTRAIT layout, a landscape source page still turns 90° to fit a
portrait-shaped slot — left upright it would be squeezed smaller and be harder
to read. On a landscape layout the 90° is already applied to everything, so
there is nothing extra to do. The back-side +180° rides on top either way.

## Which turn is +90?

`+90` is PyMuPDF's `rotate=90`, which is anticlockwise on the page. Front and
back are 180° apart either way, so page *registration* is unaffected by the
handedness — but a 1-up landscape sheet will read when turned one way rather
than the other. If the store wants the opposite handedness, swap 90 ↔ 270 in
`nup_imposer.layout_rotation`, and flip the anticlockwise turn in
`slot_position` to match, or page 1 stops landing where it should.

---

## Verified on paper

**All twelve A4 combinations pass — OSP, Konica bizhub PRO 1100, 2026-08-17.**
Run with `tools/proof_run.py --printer konica` at commit `3dbea4d`, checked
sheet by sheet against the matrix above.

> ℹ️ **Geometry ✅ below is unaffected and was never in question.** Between
> 2026-08-29 and 2026-08-30, the Konica driver was found not to honour a
> per-job duplex/simplex override at all (these checkmarks only ever proved
> the sheet layout is correct *given* duplex actually happened, which back
> then was coincidental) — since fixed via dual-queue routing; see "Not yet on
> paper" below for the full history.

| Combination | A4 | A3 | A5 |
|---|---|---|---|
| 1-up portrait / landscape | ✅ | ⏳ | ⏳ |
| 2-up portrait / landscape | ✅ | ⏳ | ⏳ |
| 4-up portrait / landscape, both directions | ✅ | ⏳ | ⏳ |
| 6-up portrait / landscape, both directions | ✅ | ⏳ | ⏳ |
| 9-up, either orientation | ⏳ | ⏳ | ⏳ |

That result settles the two open questions this model was carrying:

* **The turn direction is right.** Landscape turns 90°, page 1 lands at the
  bottom, and the sheet reads when turned clockwise. The paired flip in
  `layout_rotation` + `slot_position` does not need reversing.
* **Portrait duplex needs no back turn on the Konica.** The `duplex_back_rotation
  = 180` recorded in `PRINT_IMPOSITION.md` does not apply here: tying the 180° to
  the landscape selection is correct, and portrait at 0° prints right way up. Do
  not reintroduce a global back rotation on the strength of that older note.

### Not yet on paper

* **A3 and A5.** Only A4 has been run. Slot arithmetic is paper-size independent,
  but the Konica's duplex unit is not guaranteed to be.
* **9-up.** Never printed at any size.
* **The Epson EM-C8100 — now verified on A4 (2026-08-28).** T1 (landscape 2-up
  duplex, the case that decides the back turn) was printed on both OSP printers
  and came out correct on each. So the imposition model holds on the Epson's
  duplex unit too, not just the Konica. A3/A5/9-up on the Epson are still
  untested. See `print_server._effective_printer_key` for how a no-Konica store
  routes everything here.
* **Mixed colour + duplex**, which splits across both printers — the one case
  where a single global rule could bite.

### Store PC state, 2026-08-17

The OSP store PC ran `claude/oxygen-store-tasks-ys0j66` until this test and now
tracks `main`. That branch is **unmerged and carries a competing imposition
model** — `tools/nup_doctor.py`, `store_config.duplex_back_rotation`,
`tests/test_nup_duplex_geometry.py`, and JS mirror changes in `website/order/`.
Pulling `main` from it merges rather than switches, and conflicts in
`nup_imposer.py`, `print_planner.py` and two test files. Use
`git checkout -B main origin/main`, never a plain pull, and do not hand-resolve
those conflicts — the two models are not compatible.

---

## If a sheet ever comes out wrong

The symptom names the owner:

| What you see | Where it lives |
|---|---|
| Sheet is landscape | Imposition never ran — stale code, restart Printosky |
| Front reads 2 then 1 | Turn direction — `layout_rotation` **and** `slot_position`, flipped together |
| Back upside down, right pages | `back_rotation` |
| Back right way up, wrong slots | `effective_slot` — a mirror, not a rigid turn |
| Pages cropped at the edge | Margins or gutters in `perform_nup`, not rotation |
| Wrong sheet count | Pagination in `impose_plan`, not geometry |

`python tools/proof_run.py <pdf>` imposes all twelve without printing, so the
geometry can be read before any paper is committed; `--send --printer konica`
then prints them.

**First, decide: code or driver.** The table above blames code — but code is
identical on every box, so if a sheet is wrong on **one** printer and right on
another (run the same `proof_run … --send` on both to check), it is **not** the
imposition. It is that printer's Windows driver. The one that has actually bitten
us:

> **Back upside-down on the Epson EM-C8100, one store only (Nattika, 2026-08-28).**
> Cause: the Epson queue's driver default was `DuplexingMode = TwoSidedLongEdge`
> (duplex switched on *in the driver* during the Konica→Epson setup), which
> overrode SumatraPDF's per-job `duplexlong` and flipped the back 180°. OSP's
> Epson defaults to `OneSided` and prints correctly. Fix — match OSP, letting
> SumatraPDF drive duplex per job:
> ```powershell
> Set-PrintConfiguration -PrinterName "EPSON EM-C8100 Series" -DuplexingMode OneSided
> ```
> Keep the Epson default at **`OneSided`** on every box. A driver reinstall or
> re-image can silently reintroduce `TwoSidedLongEdge`; re-verify with
> `proof_run.py --only T1 --send --printer epson` after any driver change.

> **🛑 Konica per-job duplex/simplex override does not work at all, OSP, 2026-08-29.**
> Unlike the Epson case above (a driver *default* overriding one direction), this
> is worse: `SumatraPDF -print-settings duplexlong` / `simplex` is silently
> ignored by `KONICA MINOLTA 1100 PS` in **both directions**. The printer always
> follows whatever its Printing Preferences default currently is.
>
> Sequence that proved it (`tools/nup_final_test.py --simplex`/`--only
> 2up_landscape`, both `--send --printer konica`):
> 1. Driver default was `2-Sided` (checked in Printing Preferences → Layout).
>    `Get-PrintConfiguration` reported `DuplexingMode: OneSided` — **wrong**;
>    that cmdlet does not reflect the true default for this legacy PostScript
>    (v3) driver. A simplex job (`-print-settings ...,simplex,...`, confirmed in
>    `logs/print_server.log`) printed duplex anyway.
> 2. Unchecked `2-Sided` in Printing Preferences, saved as default. Simplex jobs
>    then printed correctly single-sided.
> 3. A duplex job (`-print-settings ...,duplexlong,...`, confirmed in the log)
>    immediately afterward printed **simplex** — the override failed in the
>    *other* direction too, now that the default had flipped.
>
> Conclusion: every previously "verified" Konica duplex result (including the
> original 16-combination N-up run) only matched because the driver's default
> happened to already be duplex at the time. It was never proof the per-job
> override worked — a false positive baked into the test until it started
> testing 2-page jobs (see `tools/nup_final_test.py` — a 1-page simplex job
> can't tell simplex from duplex, since there's nothing for a wrongly-duplexing
> printer to put on the back).
>
> This is a known class of Windows printing problem with copier/MFP PostScript
> "class" drivers: many require the app to call `DocumentProperties()` twice (a
> merge pass) for a per-job DEVMODE change to reach the driver's private data;
> a naive single-pass set (which is what most GDI print utilities, including
> SumatraPDF, do) gets silently dropped and the queue's persisted default wins.
>
> **Fixed, 2026-08-30 — not by patching the override, by not needing one.**
> Neither a SumatraPDF upgrade nor a PCL driver was pursued; instead, the same
> physical Konica was installed as a second Windows queue reusing the existing
> driver and port:
> ```powershell
> Add-Printer -Name "KONICA MINOLTA 1100 PS (Duplex)" -DriverName "KONICA MINOLTA 1100 PS" -PortName "IP_192.168.55.110"
> ```
> with its own Printing Preferences default set to `2-Sided`, while the
> original `KONICA MINOLTA 1100 PS` queue keeps the `1-Sided` default from
> step 2 above. `print_server._konica_queue_for_sides()` picks between the two
> queue names by the job's `sides` value instead of asking either driver
> instance to override its own default — see `install/INSTALL.md` for the full
> setup and `store_config.json`'s `printer_queue_names` keys
> (`konica_duplex` / `konica_simplex`). Reverified on paper:
> `--simplex --send --printer konica` printed two genuinely separate
> single-sided sheets; `--only 2up_landscape --send --printer konica` printed
> one genuine duplex sheet, back correctly turned. Both directions now work.
>
> This also caught a second, unrelated bug: once `printer_queue_names` was
> configured, `print_server.py`'s own file logging silently stopped working
> (a `logging.basicConfig()` ordering issue — see the commit fixing it). Fixed
> separately; not a driver problem.
