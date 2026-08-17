# Page rotation matrix — every combination

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

## Still to confirm on paper

Nothing here has been through a printer since the model changed to a portrait
canvas. The earlier 2-up result was measured on a landscape sheet with the
driver set to short-edge, so it does not carry over. Print one sheet of each
before trusting it at the counter:

| Combination | Status |
|---|---|
| 2-up portrait duplex | ⏳ never printed |
| 2-up landscape duplex | ⏳ never printed under this model |
| 1-up landscape duplex | ⏳ never printed — the 90/−90 rule is new |
| 4-up / 6-up / 9-up, either orientation | ⏳ never printed |
| Vertical fill direction, any layout | ⏳ never printed |

One thing to watch. `PRINT_IMPOSITION.md` recorded `duplex_back_rotation = 180`
for a **portrait** 2-up on the Konica. This model gives a portrait layout a back
turn of **0°**, because the 180° is tied to the landscape selection. If portrait
duplex comes off the Konica upside down on the back, that measurement was right
and the trigger belongs on every duplex job rather than on the orientation
choice — a one-line change in `back_rotation()`.
