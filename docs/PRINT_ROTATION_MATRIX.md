# Page rotation matrix — every combination

_Generated from the code. Regenerate with `python tools/nup_matrix.py --markdown`._

Every rotation the imposer can apply, for every combination of layout, sheet
orientation and fill direction. The numbers here come out of
`nup_imposer.impose_plan` — the same function `perform_nup` draws from — so this
table **is** the imposer, not a description of it. `tests/test_nup_rotation_matrix.py`
holds it in place.

The longer write-up of how this model was arrived at — six rounds and a lot of
wasted paper — lives in `docs/PRINT_IMPOSITION.md` on branch
`claude/oxygen-store-tasks-ys0j66`, which is not merged. Worth reading before
changing anything here.

---

## The rules

**1. For a landscape sheet, the back side turns 180°.** That is the N-up case.

**2. For single (1-up) with landscape selected, the front turns 90° and the
back turns −90°.**

Both are the same law seen twice:

> A page turns 90° only when it does not match the slot it lands in.
> Every back sheet-side then turns a further 180° — but only when the physical
> sheet is landscape.

The printer is told `duplexlong` for every layout, always. Long edge means the
flip happens about the sheet's long edge:

| Sheet | Long edge | Flip axis | Back comes up | Correction |
|---|---|---|---|---|
| Portrait | vertical | vertical | right way up | **0°** |
| Landscape | horizontal | horizontal | upside down | **180°** |

That single fact produces both rules. On 1-up landscape the page is already
turned 90° to fit, so the back is 90 + 180 = 270°, which is −90°. On N-up the
front sits at 0°, so the back is 0 + 180 = 180°.

Notice the two rules are consistent with each other: **the back is always
exactly 180° from the front**, or not turned at all. That is not a coincidence —
the duplex unit plus the reader's flip is a rigid motion of the sheet, and the
only rigid motions that map a rectangle onto itself are 0° and 180°. Ink on
paper cannot be mirrored.

## The 180° is a rigid turn, not a column swap

When the back turns 180°, **both slot axes reverse and the content turns with
them**:

```
front (2x1)              back (2x1), turned 180
+---------+---------+    +---------+---------+
| 1 @ 0   | 2 @ 0   |    | 4 @ 180 | 3 @ 180 |
+---------+---------+    +---------+---------+
                         page order reversed AND content upside down
```

Reversing columns *without* turning the content is a mirror. That was the old
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
source turns the other way where the two disagree; see "Landscape sources"
below. Cells read `page@angle`, in physical slot order (left to right, top to
bottom) on the sheet as it prints.

| Layout | Sheet | Direction | Grid | Front side | Back side | Back turn |
|---|---|---|---|---|---|---|
| 1-up | portrait | horizontal | 1×1 | 1@0° | 2@0° | 0° |
| 1-up | portrait | vertical | 1×1 | 1@0° | 2@0° | 0° |
| 1-up | landscape | horizontal | 1×1 | 1@90° | 2@270° | 180° |
| 1-up | landscape | vertical | 1×1 | 1@90° | 2@270° | 180° |
| 2-up | portrait | horizontal | 1×2 | 1@0° 2@0° | 3@0° 4@0° | 0° |
| 2-up | portrait | vertical | 1×2 | 1@0° 2@0° | 3@0° 4@0° | 0° |
| 2-up | landscape | horizontal | 2×1 | 1@0° 2@0° | 4@180° 3@180° | 180° |
| 2-up | landscape | vertical | 2×1 | 1@0° 2@0° | 4@180° 3@180° | 180° |
| 4-up | portrait | horizontal | 2×2 | 1@0° 2@0° 3@0° 4@0° | 5@0° 6@0° 7@0° 8@0° | 0° |
| 4-up | portrait | vertical | 2×2 | 1@0° 3@0° 2@0° 4@0° | 5@0° 7@0° 6@0° 8@0° | 0° |
| 4-up | landscape | horizontal | 2×2 | 1@0° 2@0° 3@0° 4@0° | 8@180° 7@180° 6@180° 5@180° | 180° |
| 4-up | landscape | vertical | 2×2 | 1@0° 3@0° 2@0° 4@0° | 8@180° 6@180° 7@180° 5@180° | 180° |
| 6-up | portrait | horizontal | 2×3 | 1@0° 2@0° 3@0° 4@0° 5@0° 6@0° | 7@0° 8@0° 9@0° 10@0° 11@0° 12@0° | 0° |
| 6-up | portrait | vertical | 2×3 | 1@0° 4@0° 2@0° 5@0° 3@0° 6@0° | 7@0° 10@0° 8@0° 11@0° 9@0° 12@0° | 0° |
| 6-up | landscape | horizontal | 3×2 | 1@0° 2@0° 3@0° 4@0° 5@0° 6@0° | 12@180° 11@180° 10@180° 9@180° 8@180° 7@180° | 180° |
| 6-up | landscape | vertical | 3×2 | 1@0° 3@0° 5@0° 2@0° 4@0° 6@0° | 12@180° 10@180° 8@180° 11@180° 9@180° 7@180° | 180° |
| 9-up | portrait | horizontal | 3×3 | 1@0° 2@0° 3@0° 4@0° 5@0° 6@0° 7@0° 8@0° 9@0° | 10@0° … 18@0° | 0° |
| 9-up | portrait | vertical | 3×3 | 1@0° 4@0° 7@0° 2@0° 5@0° 8@0° 3@0° 6@0° 9@0° | 10@0° 13@0° 16@0° … | 0° |
| 9-up | landscape | horizontal | 3×3 | 1@0° … 9@0° | 18@180° 17@180° … 10@180° | 180° |
| 9-up | landscape | vertical | 3×3 | 1@0° 4@0° 7@0° … | 18@180° 15@180° 12@180° … | 180° |

Simplex is the front row of each combination, with no back side. Add
`--simplex` to the tool to print those too.

### Reading it as sheets

`python tools/nup_matrix.py` draws each combination in its physical slot
positions, which is easier to check against paper:

```
2-up  landscape horizontal duplex   grid 2x1  back turn 180deg
  sheet 1 [FRONT]    1@0     2@0
  sheet 1 [BACK ]    4@180   3@180

6-up  landscape horizontal duplex   grid 3x2  back turn 180deg
  sheet 1 [FRONT]    1@0     2@0     3@0
                     4@0     5@0     6@0
  sheet 1 [BACK ]   12@180  11@180  10@180
                     9@180   8@180   7@180
```

---

## Grid shapes

`nup_imposer.SHEET_GRIDS`. Sheet orientation picks the shape; the layout only
says how many slots.

| | portrait | landscape |
|---|---|---|
| 1-up | 1×1 | 1×1 |
| 2-up | 1×2 (stacked) | 2×1 (side by side) |
| 4-up | 2×2 | 2×2 |
| 6-up | 2×3 | 3×2 |
| 9-up | 3×3 | 3×3 |

An explicit customer orientation always wins. On `auto`, each layout falls back
to its natural shape — 2-up landscape, 4-up portrait, 6-up landscape, 9-up
portrait — except that 2-up with a *vertical* fill direction means "two
stacked" and so resolves to a portrait sheet. That preserves what the order
page's direction toggle has always meant.

## Portrait pages do not turn into N-up slots

A portrait document must read without turning the sheet, even where turning it
would fill the slot better. On 2-up that means roughly 47% scale with white
bands either side — the ordinary portrait handout look. This is why every N-up
front side in the matrix sits at 0°.

The exception is 1-up: there is no slot to fill, so `landscape` there is the
customer deliberately asking for a turned page, and it gets one.

### Landscape sources

A landscape source page still turns 90° to fit a portrait slot — left upright
it would be squeezed smaller and be harder to read, so the objection above does
not apply. The back-side +180° rides on top exactly as in the table.

## Which turn is +90?

`+90` is PyMuPDF's `rotate=90`, which is anticlockwise on the page. Front and
back are 180° apart either way, so page *registration* is unaffected by the
handedness — but a 1-up landscape sheet will read when turned one way rather
than the other. If the store wants the opposite handedness, swap 90 ↔ 270 in
`nup_imposer.fit_rotation` and nothing else changes.

---

## Still to confirm on paper

The matrix is exact and the tests cover all 20 combinations, but only 2-up has
ever been through the printer. Print one sheet of each before trusting it at
the counter:

| Combination | Status |
|---|---|
| 2-up landscape duplex | ✅ verified on the Konica 2026-08-16 |
| 1-up landscape duplex | ⏳ never printed — the 90/−90 rule is new |
| 2-up portrait duplex | ⏳ never printed |
| 4-up / 6-up / 9-up, either orientation | ⏳ never printed |
| Vertical fill direction, any layout | ⏳ never printed |

If the doctor and the matrix agree but the paper is wrong, it is the printer,
not the code — the correction is one bit, and it lives in
`back_rotation()`.
