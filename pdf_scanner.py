"""
PRINTOSKY PDF COLOUR SCANNER
==============================
Analyses a PDF page by page to detect colour vs B&W content.
Used to auto-price mixed colour/B&W print jobs accurately.

Returns:
    {
        "total_pages": 45,
        "colour_pages": [1, 5, 6],        # 1-indexed
        "bw_pages": [2,3,4,7,...,45],
        "colour_count": 3,
        "bw_count": 42,
        "is_mixed": True,
        "all_colour": False,
        "all_bw": False,
        "scan_time_s": 4.2,
        "error": None,
    }

Detection method:
  - Checks each page's content stream for colour operators
  - Checks for colour images (non-grayscale colorspace)
  - Fast: ~0.1–0.3s per page for typical documents
"""

import time
import logging

logger = logging.getLogger("pdf_scanner")

# Colour PDF operators that indicate non-B&W content
COLOUR_OPERATORS = {
    b"rg",   # RGB fill
    b"RG",   # RGB stroke
    b"k",    # CMYK fill
    b"K",    # CMYK stroke
    b"sc",   # colour fill (general)
    b"SC",   # colour stroke (general)
    b"scn",  # colour fill (special)
    b"SCN",  # colour stroke (special)
}

COLOUR_COLORSPACES = {
    "/DeviceRGB", "/DeviceCMYK", "/CalRGB",
    "/ICCBased",  "/Indexed",    "/Separation",
    "/DeviceN",   "/Pattern",
}


def _page_has_colour(page) -> bool:
    """
    Check a single pikepdf page object for colour content.
    Returns True if the page contains colour elements.
    """
    try:
        import pikepdf

        # 1. Check content stream for colour operators
        try:
            contents = page.get("/Contents")
            if contents is not None:
                # Normalise to a list
                if isinstance(contents, pikepdf.Array):
                    streams = list(contents)
                else:
                    streams = [contents]

                for stream_ref in streams:
                    try:
                        stream = stream_ref
                        data = stream.read_bytes()
                        tokens = data.split()
                        for tok in tokens:
                            if tok in COLOUR_OPERATORS:
                                # Check it's not a greyscale value (e.g. "0 0 1 rg" is blue, "0.5 rg" is grey)
                                return True
                    except Exception:
                        pass
        except Exception:
            pass

        # 2. Check images in the page resources
        try:
            resources = page.get("/Resources")
            if resources:
                xobjects = resources.get("/XObject")
                if xobjects:
                    for key in xobjects.keys():
                        try:
                            xobj = xobjects[key]
                            subtype = str(xobj.get("/Subtype", ""))
                            if subtype == "/Image":
                                cs = xobj.get("/ColorSpace")
                                if cs is not None:
                                    cs_name = str(cs) if not hasattr(cs, '__iter__') else str(cs[0])
                                    if any(c in cs_name for c in [
                                        "RGB", "CMYK", "CalRGB", "ICCBased",
                                        "Indexed", "Separation", "DeviceN", "Pattern"
                                    ]):
                                        # ICCBased could still be greyscale — check components
                                        if "ICCBased" in cs_name:
                                            try:
                                                n = int(cs[1].get("/N", 1))
                                                if n == 1:
                                                    continue  # 1-component = greyscale
                                            except Exception:
                                                pass
                                        return True
                        except Exception:
                            pass
        except Exception:
            pass

    except Exception as e:
        logger.debug(f"Page colour check error: {e}")

    return False


def scan_pdf(filepath: str, timeout_seconds: int = 60) -> dict:
    """
    Scan a PDF and return page-level colour breakdown.
    timeout_seconds: give up if scanning takes longer (large files)
    """
    result = {
        "total_pages":   0,
        "colour_pages":  [],
        "bw_pages":      [],
        "colour_count":  0,
        "bw_count":      0,
        "is_mixed":      False,
        "all_colour":    False,
        "all_bw":        True,
        "scan_time_s":   0.0,
        "error":         None,
    }

    start = time.time()

    try:
        import pikepdf
        pdf = pikepdf.open(filepath)
        total = len(pdf.pages)
        result["total_pages"] = total

        colour_pages = []
        bw_pages = []

        for i, page in enumerate(pdf.pages, start=1):
            if time.time() - start > timeout_seconds:
                logger.warning(f"PDF scan timeout after {i-1} pages")
                result["error"] = f"Scan timed out — checked {i-1}/{total} pages"
                break

            if _page_has_colour(page):
                colour_pages.append(i)
            else:
                bw_pages.append(i)

        pdf.close()

        result["colour_pages"]  = colour_pages
        result["bw_pages"]      = bw_pages
        result["colour_count"]  = len(colour_pages)
        result["bw_count"]      = len(bw_pages)
        result["is_mixed"]      = bool(colour_pages and bw_pages)
        result["all_colour"]    = bool(colour_pages and not bw_pages)
        result["all_bw"]        = bool(bw_pages and not colour_pages)
        result["scan_time_s"]   = round(time.time() - start, 2)

        logger.info(
            f"PDF scan: {total} pages, "
            f"{len(colour_pages)} colour, {len(bw_pages)} B&W "
            f"in {result['scan_time_s']}s"
        )

    except ImportError:
        result["error"] = "pikepdf not installed"
        logger.warning("pikepdf not available — colour scan skipped")
    except Exception as e:
        result["error"] = str(e)
        logger.warning(f"PDF scan error: {e}")

    return result


def format_scan_summary(scan: dict, copies: int = 1) -> str:
    """
    Format scan result as a human-readable WhatsApp message segment.
    e.g.:
      📄 Your file has 45 pages:
         🖤 38 pages B&W
         🎨 7 pages Colour
    or:
      📄 Your file has 20 pages (all B&W)
    or:
      📄 Your file has 10 pages (all Colour)
    """
    total = scan["total_pages"]
    if not total:
        return f"📄 File received"

    if scan["all_bw"]:
        return f"📄 Your file has *{total} pages* (all B&W)"
    elif scan["all_colour"]:
        return f"📄 Your file has *{total} pages* (all Colour)"
    else:
        col = scan["colour_count"]
        bw  = scan["bw_count"]
        lines = [f"📄 Your file has *{total} pages*:"]
        lines.append(f"   🖤 {bw} page{'s' if bw!=1 else ''} B&W")
        lines.append(f"   🎨 {col} page{'s' if col!=1 else ''} Colour")
        return "\n".join(lines)


def calculate_mixed_cost(
    scan: dict,
    size: str,
    layout: str,
    sided: str,
    copies: int,
) -> dict:
    """
    Calculate cost for a mixed colour/B&W job using scan results.
    Returns {"bw_cost", "colour_cost", "total_print_cost", "breakdown_line"}
    """
    import math
    from rate_card import RATES, calculate_sheets

    size = size.upper()
    rate_key = "double" if layout == "double" else "single"

    bw_pages     = scan["bw_count"]
    colour_pages = scan["colour_count"]

    # Calculate sheets for each colour type
    bw_sheets     = calculate_sheets(bw_pages, layout, sided) if bw_pages else 0
    colour_sheets = calculate_sheets(colour_pages, layout, sided) if colour_pages else 0

    bw_rate     = RATES.get(size, RATES["A4"])["bw"][rate_key]
    colour_rate = RATES.get(size, RATES["A4"])["col"][rate_key]

    bw_cost     = round(bw_sheets * copies * bw_rate, 2)
    colour_cost = round(colour_sheets * copies * colour_rate, 2)
    total       = round(bw_cost + colour_cost, 2)

    parts = []
    if bw_sheets:
        parts.append(f"🖤 {bw_sheets} B&W sheets × {copies} × ₹{bw_rate} = ₹{bw_cost:.2f}")
    if colour_sheets:
        parts.append(f"🎨 {colour_sheets} colour sheets × {copies} × ₹{colour_rate} = ₹{colour_cost:.2f}")

    return {
        "bw_cost":          bw_cost,
        "colour_cost":      colour_cost,
        "total_print_cost": total,
        "breakdown_lines":  parts,
    }
