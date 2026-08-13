import fitz  # PyMuPDF
import io

PAPER_SIZES = {
    "A4": (595.28, 841.89),
    "A3": (841.89, 1190.55),
    "A5": (419.53, 595.28),
    "Letter": (612.0, 792.0),
    "Legal": (612.0, 1008.0),
}

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
            
            if layout_direction.lower() == "vertical":
                row = slot % rows
                col = slot // rows
            else:
                row = slot // cols
                col = slot % cols

            # 3. Double Sided (Duplex Mirroring horizontally)
            eff_col = col
            if is_duplex and is_back_page:
                eff_col = (cols - 1) - col

            slot_x0 = margin_x + eff_col * (slot_width + gutter_x)
            slot_y0 = margin_y + row * (slot_height + gutter_y)

            if in_pg_idx != -1:
                in_page = doc_in[in_pg_idx]
                in_w, in_h = in_page.rect.width, in_page.rect.height

                # Auto-detect if input page needs rotation to best fit slot orientation
                # (e.g., portrait page in landscape slot)
                slot_is_landscape = slot_width > slot_height
                page_is_portrait = in_h > in_w
                needs_rotation = (slot_is_landscape and page_is_portrait) or (not slot_is_landscape and not page_is_portrait)

                if needs_rotation:
                    rot = 90 if (is_duplex and is_back_page) else 270
                    eff_w, eff_h = in_h, in_w
                else:
                    rot = 0
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
