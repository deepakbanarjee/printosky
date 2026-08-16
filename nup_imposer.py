import fitz  # PyMuPDF
import io

PAPER_SIZES = {
    "A4": (595.28, 841.89),
    "A3": (841.89, 1190.55),
    "A5": (419.53, 595.28),
    "Letter": (612.0, 792.0),
    "Legal": (612.0, 1008.0),
}


# Scale modes offered to the customer in order-v2. Kept here because the
# imposer is the only place that actually applies them; the frontend mirrors
# these names in the print_spec.
SCALE_MODES = ("fit", "actual", "shrink", "custom")

# Content may exceed the slot by this many points before it counts as an
# overflow — covers rounding in page boxes rather than real clipping.
FIT_TOLERANCE_PT = 1.0


def resolve_scale(scale_mode: str, eff_w: float, eff_h: float,
                  slot_w: float, slot_h: float, scale_percent: float = 100.0,
                  maintain_aspect: bool = True):
    """Resolve a scale mode to (target_w, target_h, factor).

    fit     — scale to fill the slot, preserving aspect (default)
    actual  — 100%, no scaling; may overflow the slot and clip
    shrink  — like fit, but never scale *up* a page that already fits
    custom  — an explicit percentage of the source size

    ``factor`` is None when the page is stretched to the slot (aspect not
    maintained), because there is no single factor in that case.
    """
    if eff_w <= 0 or eff_h <= 0:
        return slot_w, slot_h, None

    fit_factor = min(slot_w / eff_w, slot_h / eff_h)
    mode = (scale_mode or "fit").strip().lower()

    if mode in ("actual", "original", "none"):
        factor = 1.0
    elif mode == "shrink":
        factor = min(1.0, fit_factor)
    elif mode == "custom":
        factor = max(0.01, float(scale_percent) / 100.0)
    else:  # "fit" and anything unrecognised
        if not maintain_aspect:
            return slot_w, slot_h, None
        factor = fit_factor

    return eff_w * factor, eff_h * factor, factor


def check_fit(page_w: float, page_h: float, slot_w: float, slot_h: float,
              scale_mode: str = "fit", scale_percent: float = 100.0,
              allow_rotation: bool = True) -> dict:
    """Report whether a page will fit its slot under the chosen scale mode.

    Returns a dict with ``fits``, the ``overflow_w``/``overflow_h`` in points,
    ``overflow_pct`` (worst axis, as a percentage of the slot) and the
    ``factor`` applied. Used by the planner to warn before printing, and
    mirrored by the order-v2 UI so the customer sees it at selection time.
    """
    eff_w, eff_h = page_w, page_h
    if allow_rotation:
        slot_is_landscape = slot_w > slot_h
        page_is_portrait = page_h > page_w
        if (slot_is_landscape and page_is_portrait) or (not slot_is_landscape and not page_is_portrait):
            eff_w, eff_h = page_h, page_w

    target_w, target_h, factor = resolve_scale(
        scale_mode, eff_w, eff_h, slot_w, slot_h, scale_percent)

    over_w = max(0.0, target_w - slot_w)
    over_h = max(0.0, target_h - slot_h)
    worst = 0.0
    if slot_w > 0 and slot_h > 0:
        worst = max(over_w / slot_w, over_h / slot_h) * 100.0

    return {
        "fits": over_w <= FIT_TOLERANCE_PT and over_h <= FIT_TOLERANCE_PT,
        "overflow_w": round(over_w, 2),
        "overflow_h": round(over_h, 2),
        "overflow_pct": round(worst, 1),
        "factor": None if factor is None else round(factor, 4),
        "slot_w": round(slot_w, 2),
        "slot_h": round(slot_h, 2),
        "target_w": round(target_w, 2),
        "target_h": round(target_h, 2),
    }


def duplex_mirror_axis(out_width: float, out_height: float, binding_edge: str) -> str:
    """Which slot axis mirrors on the back of a duplexed sheet.

    A duplex unit can only lay the back image down at 0 or 180 degrees — it
    cannot mirror slot positions. So when the reader flips the sheet, the slots
    on the back appear reversed along the flip axis, and the imposer has to
    pre-reverse them. Which axis that is depends on the physical edge the sheet
    is flipped about:

        flip about a VERTICAL edge (left/right) -> columns reverse
        flip about a HORIZONTAL edge (top/bottom) -> rows reverse

    and which edge is which depends on the sheet's own aspect:

        portrait  sheet: long edge = vertical,   short edge = horizontal
        landscape sheet: long edge = horizontal, short edge = vertical

    Returns "cols" or "rows".

    Content *rotation* is deliberately NOT handled here — the driver already
    rotates the back image for the binding mode it was given. Rotating it again
    in the imposed PDF is the double flip that printed upside-down backs.
    """
    sheet_is_landscape = out_width > out_height
    if str(binding_edge).lower().startswith("short"):
        edge_is_vertical = sheet_is_landscape
    else:  # long edge (default)
        edge_is_vertical = not sheet_is_landscape
    return "cols" if edge_is_vertical else "rows"

def perform_nup(
    file_bytes: bytes, cols: int, rows: int, margin_x: float = 20.0, margin_y: float = 20.0, 
    gutter_x: float = 10.0, gutter_y: float = 10.0, 
    paper_size: str = "A4", orientation: str = "Portrait", custom_width: float = 595.28, custom_height: float = 841.89,
    is_duplex: bool = False, scale_behavior: str = "Auto-Fit", maintain_aspect: bool = True, is_centered: bool = True,
    custom_scale_width: float = 200.0, custom_scale_height: float = 200.0,
    n_repeat: int = 1, is_collate: bool = False, draw_crop_marks: bool = False,
    layout_direction: str = "horizontal", binding_edge: str = "long",
    scale_mode: str | None = None, scale_percent: float = 100.0
) -> io.BytesIO:
    """Perform N-up imposition on input PDF bytes.

    ``binding_edge`` ("long"/"short") must match the duplex mode actually sent
    to the printer — it decides which slot axis is reversed on back sheets.
    See :func:`duplex_mirror_axis`. Ignored when ``is_duplex`` is False.

    ``scale_mode`` is one of :data:`SCALE_MODES`. When None the legacy
    ``scale_behavior`` argument is used instead, so existing callers keep
    their current behaviour.

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

    mirror_axis = duplex_mirror_axis(out_width, out_height, binding_edge)

    # Draw loop
    for sheet_indices, is_back_page in sheets:
        page_out = doc_out.new_page(width=out_width, height=out_height)
        
        for slot in range(slots_per_page):
            if slot >= len(sheet_indices):
                break
                
            in_pg_idx = sheet_indices[slot]
            if in_pg_idx == -1:
                pass # blank slot (e.g. padding odd page duplex)
            
            if layout_direction.lower() == "vertical":
                row = slot % rows
                col = slot // rows
            else:
                row = slot // cols
                col = slot % cols

            # 3. Double sided — reverse the slot order along the flip axis so
            # the back registers with the front once the sheet is turned over.
            eff_col, eff_row = col, row
            if is_duplex and is_back_page:
                if mirror_axis == "cols":
                    eff_col = (cols - 1) - col
                else:
                    eff_row = (rows - 1) - row

            slot_x0 = margin_x + eff_col * (slot_width + gutter_x)
            slot_y0 = margin_y + eff_row * (slot_height + gutter_y)

            if in_pg_idx != -1:
                in_page = doc_in[in_pg_idx]
                in_w, in_h = in_page.rect.width, in_page.rect.height

                # Auto-detect if input page needs rotation to best fit slot orientation
                # (e.g., portrait page in landscape slot)
                slot_is_landscape = slot_width > slot_height
                page_is_portrait = in_h > in_w
                needs_rotation = (slot_is_landscape and page_is_portrait) or (not slot_is_landscape and not page_is_portrait)

                if needs_rotation:
                    # Same rotation on both sides. A back page previously got 90
                    # where the front got 270 — a 180-degree difference on top of
                    # the driver's own back-side rotation, which printed every
                    # back sheet upside down on rotated layouts (2-up vertical).
                    rot = 270
                    eff_w, eff_h = in_h, in_w
                else:
                    rot = 0
                    eff_w, eff_h = in_w, in_h

                # 4 & 5. Scaling & Centering Logic
                if scale_mode is not None:
                    target_w, target_h, _ = resolve_scale(
                        scale_mode, eff_w, eff_h, slot_width, slot_height,
                        scale_percent, maintain_aspect)
                else:
                    # Legacy scale_behavior path (kept for existing callers).
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
