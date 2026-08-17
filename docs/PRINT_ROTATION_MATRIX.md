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

**1. Choosing landscape turns the back side 180°.** Portrait stays at 0°.

**2. On single (1-up), landscape also turns the front 90° — so the back lands
on −90°.** 90 + 180 = 270, which is −90.

On N-up, landscape does **not** turn the content: a portrait document must read
without turning the sheet, even where turning it would fill the slot better. On
2-up that means roughly 47% scale with white bands either side — the ordinary
portrait handout look. That is why every N-up front in the matrix sits at 0°,
and the back on landscape sits at exactly 180°, as specified.

1-up is the exception because there is no slot to fill: landscape there is the
customer deliberately asking for a turned page, so it gets one.

Notice the back is always exactly 180° from the front, or not turned at all.
That is not a coincidence — the duplex unit plus the reader's flip is a rigid
motion of the sheet, and the only rigid motions that map a rectangle onto
itself are 0° and 180°. Ink on paper cannot be mirrored.

## The 180° is a rigid turn, not a column swap

When the back turns 180°, **both slot axes reverse and the content turns with
them**:

```
front (1x2)          back (1x2), turned 180
+---------+          +---------+
| 1 @ 0   |          | 4 @ 180 |
+---------+          +---------+
| 2 @ 0   |          | 3 @ 180 |
+---------+          +---------+
                     page order reversed AND content upside down
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
| 2-up | landscape | horizontal | 1×2 | 1@0° 2@0° | 4@180° 3@180° | 180° |
| 2-up | landscape | vertical | 1×2 | 1@0° 2@0° | 4@180° 3@180° | 180° |
| 4-up | portrait | horizontal | 2×2 | 1@0° 2@0° 3@0° 4@0° | 5@0° 6@0° 7@0° 8@0° | 0° |
| 4-up | portrait | vertical | 2×2 | 1@0° 3@0° 2@0° 4@0° | 5@0° 7@0° 6@0° 8@0° | 0° |
| 4-up | landscape | horizontal | 2×2 | 1@0° 2@0° 3@0° 4@0° | 8@180° 7@180° 6@180° 5@180° | 180° |
| 4-up | landscape | vertical | 2×2 | 1@0° 3@0° 2@0° 4@0° | 8@180° 6@180° 7@180° 5@180° | 180° |
| 6-up | portrait | horizontal | 2×3 | 1@0° 2@0° 3@0° 4@0° 5@0° 6@0° | 7@0° 8@0° 9@0° 10@0° 11@0° 12@0° | 0° |
| 6-up | portrait | vertical | 2×3 | 1@0° 4@0° 2@0° 5@0° 3@0° 6@0° | 7@0° 10@0° 8@0° 11@0° 9@0° 12@0° | 0° |
| 6-up | landscape | horizontal | 2×3 | 1@0° 2@0° 3@0° 4@0° 5@0° 6@0° | 12@180° 11@180° 10@180° 9@180° 8@180° 7@180° | 180° |
| 6-up | landscape | vertical | 2×3 | 1@0° 4@0° 2@0° 5@0° 3@0° 6@0° | 12@180° 9@180° 11@180° 8@180° 10@180° 7@180° | 180° |
| 9-up | portrait | horizontal | 3×3 | 1@0° … 9@0° | 10@0° … 18@0° | 0° |
| 9-up | portrait | vertical | 3×3 | 1@0° 4@0° 7@0° … | 10@0° 13@0° 16@0° … | 0° |
| 9-up | landscape | horizontal | 3×3 | 1@0° … 9@0° | 18@180° 17@180° … 10@180° | 180° |
| 9-up | landscape | vertical | 3×3 | 1@0° 4@0° 7@0° … | 18@180° 15@180° 12@180° … | 180° |

Simplex is the front row of each combination, with no back side. Add
`--simplex` to the tool to print those too.

### Reading it as sheets

`python tools/nup_matrix.py` draws each combination in its physical slot
positions, which is easier to check against paper:

```
2-up  landscape horizontal duplex   portrait sheet, grid 1x2  back turn 180deg
  sheet 1 [FRONT]    1@0
                     2@0
  sheet 1 [BACK ]    4@180
                     3@180

6-up  landscape horizontal duplex   portrait sheet, grid 2x3  back turn 180deg
  sheet 1 [FRONT]    1@0     2@0
                     3@0     4@0
                     5@0     6@0
  sheet 1 [BACK ]   12@180  11@180
                    10@180   9@180
                     8@180   7@180
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

## The N-up landscape switch

`nup_imposer.TRANSPOSE_LANDSCAPE_NUP`, currently **False**.

* **False** (the matrix above): an N-up landscape selection keeps pages
  upright. Front 0°, back 180°.
* **True**: it also turns the content 90°, so the ink matches what a landscape
  sheet would have carried and pages fill their slots. Front 90°, back 270° —
  the reader turns the sheet, and the back no longer sits on 180°.

1-up is unaffected either way; it always honours the landscape choice.

### Landscape sources

A landscape source page still turns 90° to fit a portrait-shaped slot — left
upright it would be squeezed smaller and be harder to read, so the
"never turn a portrait" objection does not apply. The back-side +180° rides on
top exactly as in the table.

## Which turn is +90?

`+90` is PyMuPDF's `rotate=90`, which is anticlockwise on the page. Front and
back are 180° apart either way, so page *registration* is unaffected by the
handedness — but a 1-up landscape sheet will read when turned one way rather
than the other. If the store wants the opposite handedness, swap 90 ↔ 270 in
`nup_imposer.layout_rotation` and nothing else changes.

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
