import fitz  # PyMuPDF
import io

PAPER_SIZES = {
    "A4": (595.28, 841.89),
    "A3": (841.89, 1190.55),
    "A5": (419.53, 595.28),
    "Letter": (612.0, 792.0),
    "Legal": (612.0, 1008.0),
}

# ---------------------------------------------------------------------------
# The rotation model
# ---------------------------------------------------------------------------
#
# EVERY IMPOSED SHEET IS PORTRAIT. The whole document goes to the printer as
# portrait duplex, always -- one paper shape, one `duplexlong` token, no
# orientation flag. A layout that logically wants landscape is composed
# TRANSPOSED onto that portrait sheet (cols and rows swapped) rather than being
# handed to the driver as a landscape page.
#
# On top of that portrait canvas, two turns:
#
#   1. LAYOUT   Choosing "landscape" turns the content 90°, 1-up and N-up
#               alike, and lays the pages out in the logical landscape
#               arrangement turned 90° anticlockwise onto the portrait sheet.
#               Page 1 therefore lands at the BOTTOM; the reader turns the
#               sheet clockwise to read it. See slot_position().
#
#   2. BACKING  A back sheet-side is turned a further 180° when -- and only
#               when -- landscape was selected.
#
# The 180° is applied as a TRUE RIGID TURN: both slot axes reverse *and* the
# content turns with them. Reversing slot columns alone is a mirror, and ink on
# paper cannot be mirrored -- that is what the old `eff_col`-only code did, and
# it is why landscape 2-up duplex printed with the back upside down.
#
# The owner's stated rules:
#
#   1-up landscape   front  +90°, back  +90 + 180 = 270° (= -90°)
#   N-up landscape   front  +90°, back  +90 + 180 = 270°, slots transposed
#   any portrait     front    0°, back            =   0°
#
# Layout direction (horizontal / vertical) changes the *fill order* of the
# slots only. It never changes a rotation.

#: (cols, rows) on the portrait sheet. A landscape layout uses the same grid --
#: transposing a landscape arrangement onto a portrait sheet swaps its axes,
#: which lands on exactly these shapes.
SHEET_GRIDS = {1: (1, 1), 2: (1, 2), 4: (2, 2), 6: (2, 3), 9: (3, 3)}


def resolve_orientation(nup: int, orientation: str | None = None,
                        direction: str = "horizontal") -> str:
    """Return the LAYOUT orientation, "portrait" or "landscape".

    This is the customer's choice, not the sheet: the sheet is always portrait.
    An explicit choice always wins. On "auto" (or nothing) everything is
    portrait, which is how the store prints unless someone asks otherwise.
    """
    o = str(orientation or "auto").strip().lower()
    return o if o in ("portrait", "landscape") else "portrait"


def sheet_grid(nup: int, orientation: str | None = None,
               direction: str = "horizontal") -> tuple[int, int]:
    """Return (cols, rows) on the portrait sheet. Orientation does not change it."""
    nup = int(nup or 1)
    grid = SHEET_GRIDS.get(nup)
    if grid is not None:
        return grid
    # Unknown n-up: squarest grid that holds it, long side down the portrait
    # sheet.
    cols = int(nup ** 0.5) or 1
    while cols > 1 and nup % cols:
        cols -= 1
    rows = max(1, nup // cols)
    return min(cols, rows), max(cols, rows)


def slot_position(slot: int, cols: int, rows: int,
                  direction: str = "horizontal",
                  orientation: str = "portrait") -> tuple[int, int]:
    """Return the (col, row) on the PORTRAIT sheet that a slot index fills.

    "horizontal" fills row-major (left to right, then down); "vertical" fills
    column-major (top to bottom, then across).

    On a PORTRAIT layout that is the portrait grid, filled directly.

    On a LANDSCAPE layout the pages are laid out in the logical landscape
    arrangement -- the transpose of the portrait grid, so `rows` cols by `cols`
    rows -- and that whole arrangement is then turned 90° anticlockwise onto
    the portrait sheet. Under that turn the landscape sheet's left edge becomes
    the portrait sheet's bottom, so page 1 lands at the BOTTOM and the reader
    turns the sheet clockwise to read it::

        landscape arrangement        on the portrait sheet
        +-----+-----+                +-----+-----+
        |  1  |  2  |       -->      |  2  |  4  |
        +-----+-----+                +-----+-----+
        |  3  |  4  |                |  1  |  3  |
        +-----+-----+                +-----+-----+
    """
    if str(orientation or "").strip().lower() == "landscape":
        # Fill the logical landscape grid: its cols are the portrait rows.
        land_cols, land_rows = max(rows, 1), max(cols, 1)
        if str(direction or "").strip().lower() == "vertical":
            c, r = slot // land_rows, slot % land_rows
        else:
            c, r = slot % land_cols, slot // land_cols
        # Turn it 90° anticlockwise onto the portrait sheet.
        return r, (land_cols - 1) - c
    if str(direction or "").strip().lower() == "vertical":
        return slot // max(rows, 1), slot % max(rows, 1)
    return slot % max(cols, 1), slot // max(cols, 1)


def back_rotation(orientation: str) -> int:
    """Degrees the whole back sheet-side is turned: 180 on landscape, else 0."""
    return 180 if str(orientation or "").strip().lower() == "landscape" else 0


def layout_rotation(orientation: str, is_single_slot: bool = True) -> int:
    """Degrees the content turns because the customer chose landscape.

    Always 90 on landscape, 1-up and N-up alike. The reader turns the sheet
    clockwise, which is why `slot_position` puts page 1 at the bottom.
    """
    return 90 if str(orientation or "").strip().lower() == "landscape" else 0


def fit_rotation(page_is_portrait: bool, slot_is_landscape: bool,
                 is_single_slot: bool = True) -> int:
    """Degrees a LANDSCAPE source page turns to fit a portrait-shaped slot.

    Portrait pages never turn to fill a slot: a portrait document must read
    without turning the sheet, even where turning it would fill the slot better
    -- on 2-up that means the ordinary ~47%-scale handout look. A landscape
    source is different; left upright it would be squeezed smaller and be
    harder to read, so the objection does not apply.
    """
    if page_is_portrait:
        return 0
    return 0 if slot_is_landscape else 90


def slot_rotation(page_is_portrait: bool, slot_is_landscape: bool,
                  is_back: bool, orientation: str,
                  is_single_slot: bool = True) -> int:
    """Total rotation for one page in one slot.

    Layout turn, plus a landscape source's fit turn where the layout did not
    already supply one, plus the back-side turn.
    """
    rot = layout_rotation(orientation, is_single_slot)
    if rot == 0:
        rot = fit_rotation(page_is_portrait, slot_is_landscape, is_single_slot)
    if is_back:
        rot += back_rotation(orientation)
    return rot % 360


def effective_slot(col: int, row: int, cols: int, rows: int,
                   is_back: bool, orientation: str) -> tuple[int, int]:
    """Where a slot physically lands, after the back side's rigid turn.

    A 180° turn reverses *both* axes. Reversing one axis alone would be a
    mirror; see the module note.
    """
    if is_back and back_rotation(orientation) == 180:
        return (cols - 1) - col, (rows - 1) - row
    return col, row


def impose_plan(total_pages: int, nup: int = 1, orientation: str | None = None,
                is_duplex: bool = False, direction: str = "horizontal",
                source_is_portrait: bool = True) -> list[dict]:
    """Describe every sheet-side an imposition would produce, without a PDF.

    Returns one dict per sheet-side::

        {"sheet": 1, "side": "front", "orientation": "landscape",
         "cols": 2, "rows": 1, "back_rotation": 180,
         "slots": [{"slot": 0, "col": 0, "row": 0, "page": 1, "rotation": 0}, ...]}

    `page` is 1-based, or None for a slot padded blank. `col`/`row` are the
    physical position on the sheet, already turned for back sides.

    This is the single source of truth for the rotation matrix, the tests and
    `tools/nup_matrix.py`. `perform_nup` below applies exactly these numbers.
    """
    nup = max(1, int(nup or 1))
    layout = resolve_orientation(nup, orientation, direction)
    cols, rows = sheet_grid(nup, orientation, direction)
    slots_per_side = cols * rows
    slot_is_landscape = _slot_is_landscape(cols, rows)

    order = list(range(1, max(0, int(total_pages)) + 1))
    plan: list[dict] = []
    idx = 0
    sheet_no = 0
    while idx < len(order):
        sheet_no += 1
        sides = ["front", "back"] if is_duplex else ["front"]
        for side in sides:
            chunk = order[idx: idx + slots_per_side]
            chunk += [None] * (slots_per_side - len(chunk))
            idx += slots_per_side
            is_back = side == "back"
            slots = []
            for slot in range(slots_per_side):
                col, row = slot_position(slot, cols, rows, direction, layout)
                eff_col, eff_row = effective_slot(col, row, cols, rows, is_back, layout)
                slots.append({
                    "slot": slot,
                    "col": eff_col,
                    "row": eff_row,
                    "page": chunk[slot],
                    "rotation": slot_rotation(source_is_portrait, slot_is_landscape,
                                              is_back, layout,
                                              is_single_slot=(slots_per_side == 1)),
                })
            plan.append({
                "sheet": sheet_no,
                "side": side,
                "orientation": layout,
                "cols": cols,
                "rows": rows,
                "back_rotation": back_rotation(layout) if is_back else 0,
                "slots": slots,
            })
    return plan


def portrait_sheet(paper_size: str = "A4") -> tuple[float, float]:
    """The sheet every imposition draws on. Always portrait, never landscape."""
    w, h = PAPER_SIZES.get(paper_size, PAPER_SIZES["A4"])
    return min(w, h), max(w, h)


def _slot_is_landscape(cols: int, rows: int, paper_size: str = "A4") -> bool:
    """Whether one slot on this grid is wider than it is tall."""
    w, h = portrait_sheet(paper_size)
    return (w / max(cols, 1)) > (h / max(rows, 1))


def perform_nup(
    file_bytes: bytes, cols: int, rows: int, margin_x: float = 20.0, margin_y: float = 20.0,
    gutter_x: float = 10.0, gutter_y: float = 10.0,
    paper_size: str = "A4", orientation: str = "Portrait", custom_width: float = 595.28, custom_height: float = 841.89,
    is_duplex: bool = False, scale_behavior: str = "Auto-Fit", maintain_aspect: bool = True, is_centered: bool = True,
    custom_scale_width: float = 200.0, custom_scale_height: float = 200.0,
    n_repeat: int = 1, is_collate: bool = False, draw_crop_marks: bool = False,
    layout_direction: str = "horizontal"
) -> io.BytesIO:
    """Perform N-up imposition on input PDF bytes.

    Returns an io.BytesIO containing the imposed PDF.
    """
    doc_in = fitz.open("pdf", file_bytes)
    doc_out = fitz.open()

    # 1 & 2. Paper size. The sheet is ALWAYS portrait -- the whole document
    # goes to the printer as portrait duplex, and a landscape layout is
    # composed transposed onto that portrait sheet. `orientation` here names
    # the LAYOUT the customer asked for, never the paper.
    if paper_size == "Custom":
        out_width, out_height = min(custom_width, custom_height), max(custom_width, custom_height)
    else:
        out_width, out_height = portrait_sheet(paper_size)

    layout = resolve_orientation(cols * rows, orientation)

    slots_per_page = cols * rows
    if slots_per_page < 1:
        slots_per_page = 1; cols = 1; rows = 1

    slot_width = (out_width - (2 * margin_x) - (gutter_x * (cols - 1))) / cols
    slot_height = (out_height - (2 * margin_y) - (gutter_y * (rows - 1))) / rows

    if slot_width <= 0 or slot_height <= 0:
        slot_width = out_width / cols
        slot_height = out_height / rows

    # 6. Build Sheet Page Mapping (Sequential order)
    doc_len = len(doc_in)
    page_indices = []
    if is_collate:
        for _ in range(n_repeat):
            for p in range(doc_len):
                page_indices.append(p)
    else:
        for p in range(doc_len):
            for _ in range(n_repeat):
                page_indices.append(p)

    sheets = []
    idx = 0
    while idx < len(page_indices):
        # Front Page
        front_chunk = page_indices[idx : idx + slots_per_page]
        while len(front_chunk) < slots_per_page:
            front_chunk.append(-1)
        sheets.append((front_chunk, False))
        idx += slots_per_page

        if is_duplex:
            # Back Page (sequential next chunk)
            back_chunk = page_indices[idx : idx + slots_per_page]
            while len(back_chunk) < slots_per_page:
                back_chunk.append(-1)
            sheets.append((back_chunk, True))
            idx += slots_per_page

    # Draw loop
    for sheet_indices, is_back_page in sheets:
        page_out = doc_out.new_page(width=out_width, height=out_height)

        for slot in range(slots_per_page):
            if slot >= len(sheet_indices):
                break

            in_pg_idx = sheet_indices[slot]
            if in_pg_idx == -1:
                pass # blank slot (e.g. padding odd page duplex)

            col, row = slot_position(slot, cols, rows, layout_direction, layout)

            # 3. Double sided: the back is turned as a rigid body -- both slot
            #    axes reverse and the content turns with them (see module note).
            eff_col, eff_row = effective_slot(col, row, cols, rows,
                                              is_duplex and is_back_page,
                                              layout)

            slot_x0 = margin_x + eff_col * (slot_width + gutter_x)
            slot_y0 = margin_y + eff_row * (slot_height + gutter_y)

            if in_pg_idx != -1:
                in_page = doc_in[in_pg_idx]
                in_w, in_h = in_page.rect.width, in_page.rect.height

                # The layout turn, a landscape source's fit turn where the
                # layout did not already supply one, then the back turn.
                slot_is_landscape = slot_width > slot_height
                page_is_portrait = in_h > in_w
                rot = slot_rotation(page_is_portrait, slot_is_landscape,
                                    is_duplex and is_back_page, layout,
                                    is_single_slot=(slots_per_page == 1))

                if rot in (90, 270):
                    eff_w, eff_h = in_h, in_w
                else:
                    eff_w, eff_h = in_w, in_h

                # 4 & 5. Scaling & Centering Logic
                target_w, target_h = slot_width, slot_height
                if scale_behavior == "Original":
                    target_w, target_h = eff_w, eff_h
                elif scale_behavior == "Custom":
                    target_w, target_h = custom_scale_width, custom_scale_height
                elif scale_behavior == "Auto-Fit" and maintain_aspect:
                    scale = min(slot_width / eff_w, slot_height / eff_h)
                    target_w, target_h = eff_w * scale, eff_h * scale

                final_x0, final_y0 = slot_x0, slot_y0
                if is_centered:
                    final_x0 = slot_x0 + (slot_width - target_w) / 2
                    final_y0 = slot_y0 + (slot_height - target_h) / 2

                rect = fitz.Rect(final_x0, final_y0, final_x0 + target_w, final_y0 + target_h)
                page_out.show_pdf_page(rect, doc_in, in_pg_idx, rotate=rot)

            # 7. Crop Marks
            if draw_crop_marks:
                crop_len = 15
                crop_offset = 5
                # Top Left
                page_out.draw_line(fitz.Point(slot_x0, slot_y0 - crop_offset), fitz.Point(slot_x0, slot_y0 - crop_offset - crop_len))
                page_out.draw_line(fitz.Point(slot_x0 - crop_offset, slot_y0), fitz.Point(slot_x0 - crop_offset - crop_len, slot_y0))
                # Top Right
                page_out.draw_line(fitz.Point(slot_x0 + slot_width, slot_y0 - crop_offset), fitz.Point(slot_x0 + slot_width, slot_y0 - crop_offset - crop_len))
                page_out.draw_line(fitz.Point(slot_x0 + slot_width + crop_offset, slot_y0), fitz.Point(slot_x0 + slot_width + crop_offset + crop_len, slot_y0))
                # Bottom Left
                page_out.draw_line(fitz.Point(slot_x0, slot_y0 + slot_height + crop_offset), fitz.Point(slot_x0, slot_y0 + slot_height + crop_offset + crop_len))
                page_out.draw_line(fitz.Point(slot_x0 - crop_offset, slot_y0 + slot_height), fitz.Point(slot_x0 - crop_offset - crop_len, slot_y0 + slot_height))
                # Bottom Right
                page_out.draw_line(fitz.Point(slot_x0 + slot_width, slot_y0 + slot_height + crop_offset), fitz.Point(slot_x0 + slot_width, slot_y0 + slot_height + crop_offset + crop_len))
                page_out.draw_line(fitz.Point(slot_x0 + slot_width + crop_offset, slot_y0 + slot_height), fitz.Point(slot_x0 + slot_width + crop_offset + crop_len, slot_y0 + slot_height))

    out_stream = io.BytesIO()
    doc_out.save(out_stream)
    out_stream.seek(0)

    doc_in.close()
    doc_out.close()

    return out_stream
