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
# One law, two halves:
#
#   1. FIT      A page turns 90° only when its own orientation does not match
#               the slot it lands in. Otherwise it stays at 0°.
#
#   2. BACKING  Every back sheet-side is then turned a further 180° -- but only
#               when the physical sheet is LANDSCAPE. The printer is always
#               told `duplexlong`, so the flip happens about the sheet's long
#               edge: vertical on a portrait sheet (back comes up the right way
#               up, nothing to correct), horizontal on a landscape sheet (back
#               comes up upside down, so we turn it 180°).
#
# The 180° is applied as a TRUE RIGID TURN: both slot axes reverse *and* the
# content turns with them. Reversing slot columns alone is a mirror, and ink on
# paper cannot be mirrored -- that is what the old `eff_col`-only code did, and
# it is why landscape 2-up duplex printed with the back upside down.
#
# Both of the rules the owner stated fall straight out of this:
#
#   1-up landscape   front  +90°, back  +90 + 180 = 270° (= -90°)
#   N-up landscape   front    0°, back    0 + 180 = 180°
#   N-up portrait    front    0°, back            =   0°
#
# Layout direction (horizontal / vertical) changes the *fill order* of the
# slots only. It never changes a rotation.

#: (cols, rows) for each n-up on each sheet orientation.
SHEET_GRIDS = {
    1: {"portrait": (1, 1), "landscape": (1, 1)},
    2: {"portrait": (1, 2), "landscape": (2, 1)},
    4: {"portrait": (2, 2), "landscape": (2, 2)},
    6: {"portrait": (2, 3), "landscape": (3, 2)},
    9: {"portrait": (3, 3), "landscape": (3, 3)},
}

#: Sheet orientation used when the customer left orientation on "auto".
DEFAULT_SHEET_ORIENTATION = {1: "portrait", 2: "landscape", 4: "portrait",
                             6: "landscape", 9: "portrait"}


def resolve_orientation(nup: int, orientation: str | None = None,
                        direction: str = "horizontal") -> str:
    """Return "portrait" or "landscape" for the imposed sheet.

    An explicit customer choice always wins. On "auto" (or nothing) each n-up
    falls back to its natural shape, except 2-up, where a vertical fill order
    means "two stacked" and therefore a portrait sheet.
    """
    o = str(orientation or "auto").strip().lower()
    if o in ("portrait", "landscape"):
        return o
    if int(nup or 1) == 2 and str(direction or "").strip().lower() == "vertical":
        return "portrait"
    return DEFAULT_SHEET_ORIENTATION.get(int(nup or 1), "portrait")


def sheet_grid(nup: int, orientation: str | None = None,
               direction: str = "horizontal") -> tuple[int, int]:
    """Return (cols, rows) for this n-up on its resolved sheet orientation."""
    nup = int(nup or 1)
    sheet = resolve_orientation(nup, orientation, direction)
    grid = SHEET_GRIDS.get(nup)
    if grid is None:
        # Unknown n-up: squarest grid that holds it, long side across a
        # landscape sheet.
        cols = int(nup ** 0.5) or 1
        while cols > 1 and nup % cols:
            cols -= 1
        rows = max(1, nup // cols)
        return (max(cols, rows), min(cols, rows)) if sheet == "landscape" else (min(cols, rows), max(cols, rows))
    return grid[sheet]


def slot_position(slot: int, cols: int, rows: int,
                  direction: str = "horizontal") -> tuple[int, int]:
    """Return the (col, row) a slot index fills.

    "horizontal" fills row-major (left to right, then down); "vertical" fills
    column-major (top to bottom, then across).
    """
    if str(direction or "").strip().lower() == "vertical":
        return slot // max(rows, 1), slot % max(rows, 1)
    return slot % max(cols, 1), slot // max(cols, 1)


def back_rotation(sheet_orientation: str) -> int:
    """Degrees the whole back sheet-side is turned. 180 on landscape, else 0.

    See the note at the top of this module: `duplexlong` flips about the
    sheet's long edge, which is horizontal on a landscape sheet.
    """
    return 180 if str(sheet_orientation or "").strip().lower() == "landscape" else 0


def fit_rotation(page_is_portrait: bool, slot_is_landscape: bool,
                 is_single_slot: bool = True) -> int:
    """Degrees a page turns to match its slot: 90, or 0.

    Three cases:

    * The page already matches its slot -- nothing to do, 0°.
    * A PORTRAIT page in an N-up slot -- still 0°. A portrait document must
      read without turning the sheet, even where turning it would fill the slot
      better; on 2-up that means the ordinary ~47%-scale handout look. This is
      why every N-up front side sits at 0° regardless of sheet orientation.
    * Anything else -- 90°. That covers a LANDSCAPE source turning into a
      portrait slot (left upright it would be squeezed smaller and be harder to
      read), and the deliberate 1-up landscape choice, where there is no slot
      to fill and the customer asked for a turned page.
    """
    if page_is_portrait != slot_is_landscape:
        return 0
    if page_is_portrait and not is_single_slot:
        return 0
    return 90


def slot_rotation(page_is_portrait: bool, slot_is_landscape: bool,
                  is_back: bool, sheet_orientation: str,
                  is_single_slot: bool = True) -> int:
    """Total rotation for one page in one slot: fit, plus the back-side turn."""
    rot = fit_rotation(page_is_portrait, slot_is_landscape, is_single_slot)
    if is_back:
        rot += back_rotation(sheet_orientation)
    return rot % 360


def effective_slot(col: int, row: int, cols: int, rows: int,
                   is_back: bool, sheet_orientation: str) -> tuple[int, int]:
    """Where a slot physically lands, after the back side's rigid turn.

    A 180° turn reverses *both* axes. Reversing one axis alone would be a
    mirror; see the module note.
    """
    if is_back and back_rotation(sheet_orientation) == 180:
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
    sheet = resolve_orientation(nup, orientation, direction)
    cols, rows = sheet_grid(nup, orientation, direction)
    slots_per_side = cols * rows
    slot_is_landscape = _slot_is_landscape(cols, rows, sheet)

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
                col, row = slot_position(slot, cols, rows, direction)
                eff_col, eff_row = effective_slot(col, row, cols, rows, is_back, sheet)
                slots.append({
                    "slot": slot,
                    "col": eff_col,
                    "row": eff_row,
                    "page": chunk[slot],
                    "rotation": slot_rotation(source_is_portrait, slot_is_landscape,
                                              is_back, sheet,
                                              is_single_slot=(slots_per_side == 1)),
                })
            plan.append({
                "sheet": sheet_no,
                "side": side,
                "orientation": sheet,
                "cols": cols,
                "rows": rows,
                "back_rotation": back_rotation(sheet) if is_back else 0,
                "slots": slots,
            })
    return plan


def _slot_is_landscape(cols: int, rows: int, sheet_orientation: str,
                       paper_size: str = "A4") -> bool:
    """Whether one slot on this grid is wider than it is tall."""
    w, h = PAPER_SIZES.get(paper_size, PAPER_SIZES["A4"])
    if str(sheet_orientation or "").lower() == "landscape":
        w, h = max(w, h), min(w, h)
    else:
        w, h = min(w, h), max(w, h)
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

    # 1 & 2. Paper Size and Orientation
    if paper_size == "Custom":
        out_width, out_height = custom_width, custom_height
    else:
        out_width, out_height = PAPER_SIZES.get(paper_size, PAPER_SIZES["A4"])

    if orientation.lower() == "landscape":
        out_width, out_height = max(out_width, out_height), min(out_width, out_height)
    else:
        out_width, out_height = min(out_width, out_height), max(out_width, out_height)

    # The sheet that actually leaves the printer decides the back-side turn.
    sheet_orientation = "landscape" if out_width > out_height else "portrait"
    back_turn = back_rotation(sheet_orientation)

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

            col, row = slot_position(slot, cols, rows, layout_direction)

            # 3. Double sided: the back is turned as a rigid body -- both slot
            #    axes reverse and the content turns with them (see module note).
            eff_col, eff_row = effective_slot(col, row, cols, rows,
                                              is_duplex and is_back_page,
                                              sheet_orientation)

            slot_x0 = margin_x + eff_col * (slot_width + gutter_x)
            slot_y0 = margin_y + eff_row * (slot_height + gutter_y)

            if in_pg_idx != -1:
                in_page = doc_in[in_pg_idx]
                in_w, in_h = in_page.rect.width, in_page.rect.height

                # Turn the page into the slot only when their orientations
                # disagree, then add the back-side turn on top.
                slot_is_landscape = slot_width > slot_height
                page_is_portrait = in_h > in_w
                rot = fit_rotation(page_is_portrait, slot_is_landscape,
                                   is_single_slot=(slots_per_page == 1))
                if is_duplex and is_back_page:
                    rot += back_turn
                rot %= 360

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
