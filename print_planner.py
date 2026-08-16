import logging
import os
import shutil
import fitz  # PyMuPDF
import nup_imposer

logger = logging.getLogger("print_planner")

def plan_print_job(job_id: str, pdf_path: str, spec: dict | None, dest_dir: str) -> list[dict]:
    """Decompose a PDF file + print_spec into an ordered list of SumatraPDF print actions.
    
    Returns a list of actions. Each action dict has:
        - "pdf_path": str (path to local PDF)
        - "colour_mode": str ("bw" | "colour")
        - "copies": int
        - "sides": str ("ss" | "ds")
        - "paper_size": str | None
        - "orientation": str | None
        - "print_area_warning": dict | None — set when the chosen scale mode
          pushes content past the printable slot (see nup_imposer.check_fit)

    Also returns (actions_list, temp_dir_path).
    If no spec or invalid, returns a single default action and None for temp_dir.
    """
    # Fallback/default params from spec or defaults
    spec = spec or {}
    copies = 1
    try:
        if "copies" in spec:
            copies = max(1, int(spec["copies"]))
    except (TypeError, ValueError):
        pass

    sides = "ss"
    if spec.get("sides") in ("duplex", "ds", "duplexlong"):
        sides = "ds"
    # Print-time duplex binding edge, sent to the printer on each action. Kept
    # separate from `sides` (which drives the internal sheet-pairing logic, and
    # must stay "ss"/"ds"). Defaults to long-edge; landscape N-up flips it to
    # short-edge below so the back side registers with the front.
    out_sides = sides

    # Set when the chosen scale would push content past the printable
    # slot. Carried on every action so staff see it, not just the log.
    fit_warning = None

    paper_size = spec.get("paper_size")
    orientation = spec.get("orientation", "auto")
    colour_mode = spec.get("colour_mode", "bw")
    
    if not os.path.exists(pdf_path):
        # Return fallback/default action so we fail cleanly during send_to_printer
        return [{
            "pdf_path": pdf_path,
            "colour_mode": colour_mode,
            "copies": copies,
            "sides": sides,
            "paper_size": paper_size,
            "orientation": orientation
        }], None

    # If no print_spec parameters, return single fallback action
    if not spec:
        return [{
            "pdf_path": pdf_path,
            "colour_mode": "auto",
            "copies": copies,
            "sides": None,
            "paper_size": None,
            "orientation": None
        }], None

    # Prepare temp directory
    temp_dir = os.path.join(dest_dir, f"temp_{job_id}")
    os.makedirs(temp_dir, exist_ok=True)
    current_pdf = pdf_path

    try:
        # Open source doc to get page count
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        doc.close()
        
        if total_pages <= 0:
            raise ValueError("Input PDF is empty or invalid")

        # 1. Slice pages_included if present
        pages_included = spec.get("pages_included") or []
        current_colour_pages = spec.get("colour_pages") or []
        
        # Ensure values are unique and sorted
        pages_included = sorted(list(set(int(p) for p in pages_included if p is not None)))
        current_colour_pages = set(int(p) for p in current_colour_pages if p is not None)

        if pages_included:
            sliced_path = os.path.join(temp_dir, "sliced.pdf")
            doc = fitz.open(pdf_path)
            # Filter pages to valid range [1, total_pages]
            valid_pages = [p for p in pages_included if 1 <= p <= total_pages]
            if not valid_pages:
                doc.close()
                raise ValueError("No valid pages found in pages_included")
            
            # Select pages (0-based indices)
            doc.select([p - 1 for p in valid_pages])
            doc.save(sliced_path)
            doc.close()
            current_pdf = sliced_path
            
            # Map original colour_pages to the new sliced page list
            mapped_colour_pages = []
            for new_idx, orig_page in enumerate(valid_pages):
                if orig_page in current_colour_pages:
                    mapped_colour_pages.append(new_idx + 1)
            current_colour_pages = mapped_colour_pages
            total_pages = len(valid_pages)
        else:
            current_colour_pages = sorted(list(current_colour_pages))

        # 2. Impose N-up
        nup = 1
        try:
            nup = int(spec.get("nup", 1))
        except (TypeError, ValueError):
            pass

        scale_mode = str(spec.get("scale_mode") or "fit").strip().lower()
        if scale_mode not in nup_imposer.SCALE_MODES:
            scale_mode = "fit"
        try:
            scale_percent = float(spec.get("scale_percent") or 100.0)
        except (TypeError, ValueError):
            scale_percent = 100.0

        # A 1-up job normally skips imposition entirely, so a non-default scale
        # would be silently dropped. Route it through a 1x1 imposition instead.
        needs_scale_pass = nup == 1 and scale_mode != "fit"

        if nup > 1 or needs_scale_pass:
            # Determine Grid & default orientation
            # 2-up -> 2x1 landscape, 4-up -> 2x2 portrait, 6-up -> 3x2 landscape, 9-up -> 3x3 portrait
            nup_map = {
                2: (2, 1, "Landscape"),
                4: (2, 2, "Portrait"),
                6: (3, 2, "Landscape"),
                9: (3, 3, "Portrait")
            }
            grid = nup_map.get(nup, (2, 2, "Portrait")) # default 4-up shape if unknown
            if nup == 1:
                # Scale-only pass: one slot, sheet keeps the requested orientation.
                grid = (1, 1, "Landscape" if str(orientation).lower() == "landscape" else "Portrait")
            cols, rows, nup_orient = grid
            nup_dir = str(spec.get("nup_direction", "horizontal")).lower()
            # 2-up: horizontal = two side-by-side (landscape); vertical = two
            # stacked, page 1 on top (1 col x 2 rows, portrait).
            if nup == 2 and nup_dir == "vertical":
                cols, rows, nup_orient = 1, 2, "Portrait"
            # The imposition below bakes the final orientation into the imposed
            # PDF's page geometry (via nup_orient). Do NOT also pass an
            # orientation flag to the printer — SumatraPDF would apply it a
            # second time and flip the sheet (a landscape 2-up came out
            # portrait). Let the printer honour the imposed page as-is.
            orientation = None

            # Landscape N-up duplex must bind on the SHORT (top) edge: the pages
            # sit side-by-side on a landscape sheet, so the back only registers
            # with the front when the sheet flips top-to-bottom. Long-edge
            # binding mis-aligns it (confirmed on the Konica). Portrait N-up and
            # 2-up vertical keep long-edge.
            binding_edge = "long"
            if sides == "ds" and nup_orient.lower() == "landscape":
                out_sides = "duplexshort"
                binding_edge = "short"

            # Read sliced file bytes
            with open(current_pdf, "rb") as f:
                pdf_bytes = f.read()

            # binding_edge must be the same edge `out_sides` asks the printer
            # for — the imposer reverses back-sheet slots along the axis the
            # sheet is flipped about. If these two disagree the back sheets
            # come out mirrored.
            # Warn (loudly, in the log and on the action) when the chosen scale
            # will push content past the printable slot — "actual size" on a
            # page larger than the paper silently clips otherwise.
            fit_report = _check_print_area(
                current_pdf, cols, rows, paper_size or "A4", nup_orient,
                scale_mode, scale_percent)
            if fit_report and not fit_report["fits"]:
                fit_warning = fit_report
                logger.warning(
                    "Job %s: content overflows the print area by %.1f%% "
                    "(scale=%s, %d-up %s %s) — output will be clipped",
                    job_id, fit_report["overflow_pct"], scale_mode, nup,
                    paper_size or "A4", nup_orient)

            imposed_stream = nup_imposer.perform_nup(
                pdf_bytes, cols=cols, rows=rows,
                paper_size=paper_size or "A4",
                orientation=nup_orient,
                is_duplex=(sides == "ds"),
                layout_direction=nup_dir,
                binding_edge=binding_edge,
                scale_mode=scale_mode,
                scale_percent=scale_percent
            )
            
            imposed_path = os.path.join(temp_dir, "imposed.pdf")
            with open(imposed_path, "wb") as f:
                f.write(imposed_stream.getvalue())
            
            # Map logical colour pages to imposed sheet page numbers (1-based)
            current_colour_pages = get_imposed_colour_sheets(
                total_logical_pages=total_pages,
                current_colour_pages=set(current_colour_pages),
                cols=cols, rows=rows,
                is_duplex=(sides == "ds")
            )
            
            current_pdf = imposed_path
            doc = fitz.open(current_pdf)
            total_pages = len(doc)
            doc.close()

        # 3. Mixed Colour Grouping & Splitting
        if colour_mode == "mixed":
            sheet_modes = [] # list of (mode, list_of_pages)
            current_colour_pages = set(current_colour_pages)

            if sides == "ds":
                # For duplex, pair sheets (1-2, 3-4, etc.)
                # If either page in a sheet contains colour, the whole sheet is colour.
                for i in range(1, total_pages + 1, 2):
                    p1 = i
                    p2 = i + 1
                    is_sheet_colour = (p1 in current_colour_pages) or (p2 <= total_pages and p2 in current_colour_pages)
                    mode = "colour" if is_sheet_colour else "bw"
                    pages = [p1]
                    if p2 <= total_pages:
                        pages.append(p2)
                    sheet_modes.append((mode, pages))
            else:
                # Simplex
                for i in range(1, total_pages + 1):
                    mode = "colour" if i in current_colour_pages else "bw"
                    sheet_modes.append((mode, [i]))

            # Group consecutive sheets of the same mode
            sections = [] # list of (mode, list_of_pages)
            if sheet_modes:
                curr_mode, curr_pages = sheet_modes[0]
                for mode, pages in sheet_modes[1:]:
                    if mode == curr_mode:
                        curr_pages.extend(pages)
                    else:
                        sections.append((curr_mode, curr_pages))
                        curr_mode = mode
                        curr_pages = list(pages)
                sections.append((curr_mode, curr_pages))

            # If there is only one section, return it directly
            if len(sections) == 1:
                return [{
                    "pdf_path": current_pdf,
                    "colour_mode": sections[0][0],
                    "copies": copies,
                    "sides": out_sides,
                    "paper_size": paper_size,
                    "orientation": orientation,
                    "print_area_warning": fit_warning
                }], temp_dir

            # Otherwise, split into multiple sequential PDF files
            actions = []
            for idx, (mode, pages) in enumerate(sections):
                sub_pdf_path = os.path.join(temp_dir, f"sub_job_{idx+1}_{mode}.pdf")
                doc = fitz.open(current_pdf)
                doc.select([p - 1 for p in pages])
                doc.save(sub_pdf_path)
                doc.close()

                actions.append({
                    "pdf_path": sub_pdf_path,
                    "colour_mode": mode,
                    "copies": copies,
                    "sides": out_sides,
                    "paper_size": paper_size,
                    "orientation": orientation,
                    "print_area_warning": fit_warning
                })
            return actions, temp_dir

        else:
            # Plain B&W or Color
            final_colour = "colour" if colour_mode == "col" else "bw"
            return [{
                "pdf_path": current_pdf,
                "colour_mode": final_colour,
                "copies": copies,
                "sides": out_sides,
                "paper_size": paper_size,
                "orientation": orientation,
                "print_area_warning": fit_warning
            }], temp_dir

    except Exception as e:
        logger.error("Error planning print job %s: %s", job_id, e)
        # Clean up if error occurred during planning
        cleanup_temp_dir(temp_dir)
        # Return fallback action
        return [{
            "pdf_path": pdf_path,
            "colour_mode": "auto",
            "copies": copies,
            "sides": None,
            "paper_size": None,
            "orientation": None
        }], None


def _check_print_area(pdf_path: str, cols: int, rows: int, paper_size: str,
                      orientation: str, scale_mode: str, scale_percent: float,
                      margin: float = 20.0, gutter: float = 10.0) -> dict | None:
    """Fit-check the widest source page against one imposition slot.

    Mirrors the slot maths in nup_imposer.perform_nup. Returns None when the
    PDF cannot be read — a fit check must never block a print.
    """
    try:
        out_w, out_h = nup_imposer.PAPER_SIZES.get(
            paper_size, nup_imposer.PAPER_SIZES["A4"])
        if str(orientation).lower() == "landscape":
            out_w, out_h = max(out_w, out_h), min(out_w, out_h)
        else:
            out_w, out_h = min(out_w, out_h), max(out_w, out_h)

        slot_w = (out_w - 2 * margin - gutter * (cols - 1)) / cols
        slot_h = (out_h - 2 * margin - gutter * (rows - 1)) / rows
        if slot_w <= 0 or slot_h <= 0:
            slot_w, slot_h = out_w / cols, out_h / rows

        worst = None
        with fitz.open(pdf_path) as doc:
            for page in doc:
                report = nup_imposer.check_fit(
                    page.rect.width, page.rect.height, slot_w, slot_h,
                    scale_mode=scale_mode, scale_percent=scale_percent)
                if worst is None or report["overflow_pct"] > worst["overflow_pct"]:
                    worst = report
        return worst
    except Exception as e:
        logger.debug("Print-area check skipped for %s: %s", pdf_path, e)
        return None


def get_imposed_colour_sheets(total_logical_pages: int, current_colour_pages: set[int], cols: int, rows: int, is_duplex: bool) -> list[int]:
    """Helper to simulate imposition mapping and determine which sheets are colour."""
    slots_per_page = cols * rows
    if slots_per_page < 1:
        slots_per_page = 1; cols = 1; rows = 1
        
    page_indices = list(range(total_logical_pages))
    sheets = []
    idx = 0
    while idx < len(page_indices):
        # Front Page
        front_chunk = page_indices[idx : idx + slots_per_page]
        sheets.append(front_chunk)
        idx += slots_per_page
        
        if is_duplex:
            # Back Page (sequential next chunk)
            back_chunk = page_indices[idx : idx + slots_per_page]
            sheets.append(back_chunk)
            idx += slots_per_page
                
    colour_sheets = []
    for sheet_idx, chunk in enumerate(sheets):
        has_colour = False
        for p in chunk:
            if p != -1 and (p + 1) in current_colour_pages:
                has_colour = True
                break
        if has_colour:
            colour_sheets.append(sheet_idx + 1)
    return colour_sheets


def cleanup_temp_dir(temp_dir: str | None):
    """Recursively delete temp directory if present."""
    if not temp_dir:
        return
    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            logger.info("Cleaned up temp directory: %s", temp_dir)
    except Exception as e:
        logger.warning("Failed to clean up temp directory %s: %s", temp_dir, e)
