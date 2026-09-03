import logging
import os
import shutil
import fitz  # PyMuPDF
import nup_imposer
import pdf_scaler

logger = logging.getLogger("print_planner")


def _report(check: str, ok: bool, detail: str) -> None:
    """Alert, never a bare log — a print that quietly ignored what the customer
    asked for is exactly the silent failure CLAUDE.md forbids."""
    try:
        from ops_watchdog import report
        report(check, ok, detail)
    except Exception as exc:                      # never break a print over this
        logger.warning("ops_watchdog report failed (%s): %s", check, exc)


def resolve_scale(spec: dict, nup: int, nup_orient: str) -> tuple[str | None, int | None]:
    """The scale this job actually gets: (mode, percent), or (None, None).

    Scaling is 1-up only (owner, 2026-08-30): N-up already IS a fit, so a
    scale on an N-up job is a UI leak and is dropped loudly rather than
    half-honoured. 1-up landscape supports fit and actual through the imposer;
    custom % there needs a rotated target box that has never been proved on
    paper, so it is dropped too, and says so.

    Absent scale returns (None, None) — the whole safety property: a spec
    without it plans exactly as it did before this existed.
    """
    scale = spec.get("scale") or {}
    mode = str(scale.get("mode") or "").strip().lower()
    if not mode:
        return None, None
    if mode not in pdf_scaler.MODES:
        _report("print_planner.scale_unknown_mode", False,
                f"print_spec asked for scale mode {mode!r}, which does not exist — "
                f"printing unscaled. Valid modes: {', '.join(pdf_scaler.MODES)}.")
        return None, None

    percent = pdf_scaler.clamp_percent(scale.get("percent")) if mode == "custom" else None
    if mode == "custom" and percent is None:
        _report("print_planner.scale_bad_percent", False,
                f"print_spec asked for a custom scale with percent="
                f"{scale.get('percent')!r}, which is not a number — printing unscaled.")
        return None, None

    if nup != 1:
        _report("print_planner.scale_on_nup", False,
                f"print_spec carried scale={mode!r} on a {nup}-up job. N-up always "
                f"fits to the slot, so the scale was ignored — the UI should not "
                f"have offered it.")
        return None, None

    if nup_orient == "landscape" and mode == "custom":
        _report("print_planner.scale_custom_landscape", False,
                f"custom {percent}% on a 1-up LANDSCAPE job is not supported yet "
                f"(the rotated target box is unproved on paper) — printed at the "
                f"landscape default instead. Use portrait, or fit/actual.")
        return None, None

    return mode, percent

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
    # The printer is told one thing, always: long-edge duplex. The back-side
    # correction for landscape sheets is a 180-degree rigid turn baked into the
    # imposition (see the rotation model in nup_imposer). Asking the driver for
    # short-edge as well would apply that same turn a second time and cancel it.
    out_sides = sides

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
            "orientation": orientation,
            "scale_applied": False,
        }], None

    # If no print_spec parameters, return single fallback action
    if not spec:
        return [{
            "pdf_path": pdf_path,
            "colour_mode": "auto",
            "copies": copies,
            "sides": None,
            "paper_size": None,
            "orientation": None,
            "scale_applied": False,
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

        # Grid and sheet orientation come from the one rotation model in
        # nup_imposer, so the planner, the imposer, the matrix tool and the
        # tests can never drift apart. An explicit customer orientation wins;
        # "auto" falls back to each n-up's natural shape (2-up -> landscape
        # unless stacked vertically, 4-up -> portrait, 6-up -> landscape,
        # 9-up -> portrait).
        nup_dir = str(spec.get("nup_direction", "horizontal")).lower()
        nup_orient = nup_imposer.resolve_orientation(nup, orientation, nup_dir)
        cols, rows = nup_imposer.sheet_grid(nup, orientation, nup_dir)

        # 2a. Scaling — Fit / Actual size / Custom %, baked into the PDF.
        #
        # Nothing is asked of the driver: the Konica has already been caught
        # ignoring per-job overrides, so the geometry goes in the file (the same
        # reason the imposer exists). `scale_applied` tells send_to_printer to
        # add SumatraPDF's `noscale` as a guard on top.
        #
        # A spec with no scale leaves every line below inert, so such a job
        # plans exactly as it did before this existed.
        scale_mode, scale_percent = resolve_scale(spec, nup, nup_orient)
        scale_applied = False

        if scale_mode and nup_orient != "landscape":
            # Pass-through path: bake it here, because nothing else will touch
            # this file on its way to the printer.
            try:
                with open(current_pdf, "rb") as f:
                    scaled = pdf_scaler.apply_scale(
                        f.read(), scale_mode, scale_percent, paper_size or "A4")
                if scaled:
                    scaled_path = os.path.join(temp_dir, "scaled.pdf")
                    with open(scaled_path, "wb") as f:
                        f.write(scaled)
                    current_pdf = scaled_path
                    scale_applied = True
                    logger.info("job %s: baked scale=%s%s", job_id, scale_mode,
                                f" {scale_percent}%" if scale_percent else "")
            except Exception as exc:
                # Print unscaled rather than not print — but never silently.
                _report("print_planner.scale_failed", False,
                        f"job {job_id}: could not apply scale={scale_mode!r} "
                        f"({type(exc).__name__}: {exc}) — printing unscaled.")

        # 1-up on a landscape sheet is still an imposition: the page turns 90
        # degrees on the front and -90 on the back. 1-up portrait is a pure
        # pass-through and never touches the imposer.
        if nup > 1 or nup_orient == "landscape":
            # The imposition below bakes the final orientation into the imposed
            # PDF's page geometry (via nup_orient). Do NOT also pass an
            # orientation flag to the printer — SumatraPDF would apply it a
            # second time and flip the sheet (a landscape 2-up came out
            # portrait). Let the printer honour the imposed page as-is.
            orientation = None

            # Read sliced file bytes
            with open(current_pdf, "rb") as f:
                pdf_bytes = f.read()

            # 1-up landscape with a scale: the imposition is the only thing
            # that touches this file, so it does the scaling too, through
            # parameters perform_nup has always had. Absent a scale we pass
            # nothing and it keeps its "Auto-Fit" default — which is what every
            # job on the verified rotation matrix does.
            impose_kwargs = {}
            if scale_mode and nup_orient == "landscape":
                impose_kwargs["scale_behavior"] = (
                    "Original" if scale_mode == "actual" else "Auto-Fit")
                scale_applied = True

                # Actual size on a landscape sheet is only achievable while the
                # turned page still fits: an A4 source is 297mm wide once turned
                # and the slot is 210mm. The imposer fits it rather than letting
                # content run off the paper, and says so here — silently
                # printing a smaller sheet than "Actual size" promised is the
                # kind of quiet wrong answer docs/FAIL_LOUD.md exists to stop.
                downgraded = []
                impose_kwargs["on_downgrade"] = downgraded.append

            imposed_stream = nup_imposer.perform_nup(
                pdf_bytes, cols=cols, rows=rows,
                paper_size=paper_size or "A4",
                orientation=nup_orient,
                is_duplex=(sides == "ds"),
                layout_direction=nup_dir,
                **impose_kwargs
            )
            
            if impose_kwargs.get("on_downgrade") and downgraded:
                first = downgraded[0]
                _report("print_planner.scale_actual_landscape", False,
                        f"job {job_id}: Actual size does not fit a 1-up "
                        f"landscape {paper_size or 'A4'} sheet — the page is "
                        f"{first['page_w']:.0f}x{first['page_h']:.0f}pt turned "
                        f"and the slot is {first['slot_w']:.0f}x"
                        f"{first['slot_h']:.0f}pt. Printed fitted at "
                        f"{first['scale'] * 100:.0f}% instead of losing the "
                        f"edges ({len(downgraded)} page(s)). Use Fit, or "
                        f"portrait, if true size matters.")

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
                    "scale_applied": scale_applied,
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
                    "scale_applied": scale_applied,
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
                "scale_applied": scale_applied,
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
            "orientation": None,
            "scale_applied": False,
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
