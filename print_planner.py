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

        if nup > 1:
            # Determine Grid & default orientation
            # 2-up -> 2x1 landscape, 4-up -> 2x2 portrait, 6-up -> 3x2 landscape, 9-up -> 3x3 portrait
            nup_map = {
                2: (2, 1, "Landscape"),
                4: (2, 2, "Portrait"),
                6: (3, 2, "Landscape"),
                9: (3, 3, "Portrait")
            }
            grid = nup_map.get(nup, (2, 2, "Portrait")) # default 4-up shape if unknown
            cols, rows, nup_orient = grid
            orientation = nup_orient.lower() # force landscape/portrait

            # Read sliced file bytes
            with open(current_pdf, "rb") as f:
                pdf_bytes = f.read()

            imposed_stream = nup_imposer.perform_nup(
                pdf_bytes, cols=cols, rows=rows,
                paper_size=paper_size or "A4",
                orientation=nup_orient,
                is_duplex=(sides == "ds"),
                layout_direction=spec.get("nup_direction", "horizontal")
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
                    "sides": sides,
                    "paper_size": paper_size,
                    "orientation": orientation
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
                    "sides": sides,
                    "paper_size": paper_size,
                    "orientation": orientation
                })
            return actions, temp_dir

        else:
            # Plain B&W or Color
            final_colour = "colour" if colour_mode == "col" else "bw"
            return [{
                "pdf_path": current_pdf,
                "colour_mode": final_colour,
                "copies": copies,
                "sides": sides,
                "paper_size": paper_size,
                "orientation": orientation
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
