"""
PRINTOSKY RATE CARD ENGINE
===========================
v2.0 — March 2026

Calculates print job cost based on:
- Paper type / size (A4 B&W, A4 Colour, Legal, A3, Bond, OHP, Stamp, Special)
- Colour mode (bw / col)
- Sides (ss = single side, ds = double side)
- Layout (1-up, 2-up, 4-up)
- Copies
- Finishing (spiral, wiro, staple, soft, project, record, lamination, etc.)
- Student discount flag
- Urgent surcharge flag

BILLING RULE (confirmed with owner):
  Rates are PER SHEET (not per page).
  DS colour rate > SS colour rate (same total cost if you double-side colour).
  DS B&W rate = SS B&W rate per sheet (so DS saves customer ~50% on B&W).
  Sheets for DS = ceil(pages/2), rounded UP to next even number.
  Layout 2-up: pages = ceil(original/2) before applying sides rule.
  Layout 4-up: pages = ceil(original/4) before applying sides rule.
"""

import math
import logging

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — PRINT RATE TABLES
# Each entry: paper_type → sides → rate_per_sheet (₹)
# For tiered rates (colour by sheet count), see get_print_rate()
# ─────────────────────────────────────────────────────────────────────────────

PRINT_RATES = {
    # A4 B&W — same rate SS and DS (billing per sheet)
    "A4_BW":              {"ss": 3.0, "ds": 3.0},
    "A4_BW_student_100":  {"ss": 2.0, "ds": 2.0},   # student, ≤100 sheets
    "A4_BW_student_100p": {"ss": 1.5, "ds": 1.5},   # student, >100 sheets

    # A4 Colour — tiered by total sheet count (see get_print_rate)
    "A4_col_30":          {"ss": 10.0, "ds": 20.0},  # ≤30 sheets
    "A4_col_50":          {"ss": 9.0,  "ds": 18.0},  # 31–50 sheets
    "A4_col_50p":         {"ss": 8.0,  "ds": 16.0},  # >50 sheets

    # A4 Special paper
    "A4_bond_col":        {"ss": 15.0},
    "A4_bond_bw":         {"ss": 5.0},
    "A4_220gsm":          {"ss": 20.0},
    "A4_OHP":             {"ss": 30.0},
    "A4_stamp":           {"ss": 30.0},

    # Legal
    "Legal_BW":           {"ss": 4.0,  "ds": 5.0},
    "Legal_BW_green":     {"ss": 5.0,  "ds": 6.0},   # green paper
    "Legal_col":          {"ss": 15.0, "ds": 30.0},

    # A3
    "A3_BW":              {"ss": 5.0, "ds": 5.0},
    "A3_col":             {"ss": 20.0, "ds": 40.0},
}

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — BINDING / FINISHING RATES
# ─────────────────────────────────────────────────────────────────────────────

# Spiral A4: tiered by sheet count — (max_sheets, price) pairs
SPIRAL_A4_TIERS = [
    (30,  30),
    (70,  40),
    (100, 50),
    (130, 60),
    (150, 80),
    (170, 90),
    (200, 120),
    (250, 150),
]

SPIRAL_A3_START = 80  # starting rate for A3 spiral

# Soft binding (with print) — tiered by sheet count
SOFT_BINDING_TIERS = [
    (70,  80),
    (100, 110),
    (130, 120),
    (150, 140),
    (200, 160),
    (250, 180),
]
SOFT_BINDING_WITHOUT_PRINT = 100  # minimum without print

# Project binding — by cover type
PROJECT_BINDING_RATES = {
    "white":  220,
    "pink":   220,
    "blue":   220,
    "green":  220,
    "gold":   250,
    "silver": 250,
    "custom": 250,
}

# Wiro binding — staff quotes manually, approximate tiers similar to spiral
WIRO_A4_TIERS = SPIRAL_A4_TIERS  # same tiers as spiral for now

BINDING_RATES = {
    "none":     {"price": 0,   "label": "No binding",         "outsourced": False},
    "staple":   {"price": 0,   "label": "Staple",             "outsourced": False},
    "spiral":   {"price": None,"label": "Spiral binding",     "outsourced": False, "tiered": True},
    "wiro":     {"price": None,"label": "Wiro binding",       "outsourced": False, "tiered": True},
    "soft":     {"price": None,"label": "Soft binding",       "outsourced": False, "tiered": True},
    "project":  {"price": None,"label": "Project binding",    "outsourced": True,  "tiered": False},
    "record":   {"price": 400, "label": "Record binding (A3)","outsourced": True,  "tiered": False},
    "lam_sheet":{"price": 60,  "label": "Sheet lamination (A4)","outsourced": False,"tiered": False},
    "lam_roll": {"price": None,"label": "Roll lamination",    "outsourced": True,  "tiered": False},
    "lam_cover":{"price": 50,  "label": "Cover lamination",   "outsourced": True,  "tiered": False},
    "id_card":  {"price": None,"label": "ID card printing",   "outsourced": False, "tiered": False},
    "thermal":  {"price": None,"label": "Thermal/sheet binding","outsourced": False,"tiered": True},
}

URGENT_SURCHARGE = 20  # applies to soft + project binding only
URGENT_ELIGIBLE  = {"soft", "project"}

LAMINATION_RATES = {
    "normal":    40,
    "with_col":  50,   # with colour copy (Aadhar, RC, licence)
    "a4":        60,
    "a3_bw":     100,
    "a3_col":    120,
}

THERMAL_BINDING_TIERS = [
    (50,  60),
    (100, 80),
]

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — OTHER SERVICE RATES
# ─────────────────────────────────────────────────────────────────────────────

SCANNING_RATES = {
    "standard_50":  10,   # per sheet, ≤50
    "standard_100": 7,    # 51–100 (average of 6–8)
    "standard_100p":5,    # >100
    "special":      2,    # Sini/Ujjwala special rate (customer profile override)
}

DTP_RATES = {
    "malayalam": 40,
    "english":   40,
    "hindi":     60,
}

DELIVERY_CHARGE = 30

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — FINISHING TYPE METADATA (for UI dropdowns)
# ─────────────────────────────────────────────────────────────────────────────

FINISHING_INHOUSE    = ["none", "staple", "spiral", "wiro", "lam_sheet", "id_card"]
FINISHING_OUTSOURCED = ["lam_roll", "lam_cover", "project", "record", "thermal"]
FINISHING_URGENT_OK  = list(URGENT_ELIGIBLE)

FINISHING_DISPLAY = {
    "none":     "No Finishing",
    "staple":   "Staple",
    "spiral":   "Spiral Binding",
    "wiro":     "Wiro Binding",
    "soft":     "Soft Binding",
    "project":  "Project Binding",
    "record":   "Record Binding",
    "lam_sheet":"Sheet Lamination",
    "lam_roll": "Roll Lamination",
    "lam_cover":"Cover Lamination",
    "id_card":  "ID Card Printing",
    "thermal":  "Thermal Binding",
}

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — BACKWARD-COMPAT STRUCTURES (used by existing watcher/bot code)
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_RATES = {
    "A4": {"bw": {"single": 3.0, "double": 3.0},
           "col":{"single": 10.0,"double": 20.0}},
    "A3": {"bw": {"single": 5.0, "double": 5.0},
           "col":{"single": 20.0,"double": 40.0}},
}
RATES = {k: {c: dict(v) for c, v in v2.items()} for k, v2 in _DEFAULT_RATES.items()}
FINISHING_RATES = {
    "none":    {"price": 0,   "label": "No finishing",   "staff_quote": False},
    "staple":  {"price": 0,   "label": "Staple",         "staff_quote": False},
    "spiral":  {"price": 30,  "label": "Spiral binding", "staff_quote": True},
    "wiro":    {"price": 50,  "label": "Wiro binding",   "staff_quote": True},
    "soft":    {"price": 80,  "label": "Soft binding",   "staff_quote": True},
    "project": {"price": 200, "label": "Project binding","staff_quote": True},
    "record":  {"price": 400, "label": "Record binding", "staff_quote": False},
    "lam_sheet":{"price":60,  "label": "Sheet lam",      "staff_quote": False},
}

# Multiple-up divisors (legacy support)
MULTIUP_DIVISORS = {"2up": 2, "4up": 4, "6up": 6, "9up": 9}

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 — CORE CALCULATION FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def calc_sheets(pages: int, sides: str = "ss", layout: str = "1-up") -> int:
    """
    Convert page count → physical sheet count for billing.

    Args:
        pages:  Number of pages in the document (or page range).
        sides:  'ss' (single-side) or 'ds' (double-side).
        layout: '1-up' | '2-up' | '4-up'  — pages per sheet face.

    Returns:
        Number of sheets to bill.

    Rules:
        1. Apply layout first: 2-up → pages = ceil(pages/2)
        2. Apply sides: ds → sheets = ceil(pages/2) rounded to next even
           ss → sheets = pages
    """
    # Step 1: layout reduction
    divisor = {"1-up": 1, "2-up": 2, "4-up": 4}.get(layout, 1)
    pages = math.ceil(pages / divisor)

    # Step 2: sides
    if sides == "ds":
        sheets = math.ceil(pages / 2)
        if sheets % 2 != 0:
            sheets += 1   # round to next even number (owner requirement)
    else:
        sheets = pages

    return max(1, sheets)


def get_spiral_rate(sheets: int, size: str = "A4") -> int:
    """Look up spiral binding rate for given sheet count and paper size."""
    if size.upper() == "A3":
        return SPIRAL_A3_START
    for max_sheets, price in SPIRAL_A4_TIERS:
        if sheets <= max_sheets:
            return price
    return SPIRAL_A4_TIERS[-1][1]  # cap at max tier price


def get_soft_binding_rate(sheets: int, with_print: bool = True) -> int:
    """Look up soft binding rate for given sheet count."""
    if not with_print:
        return SOFT_BINDING_WITHOUT_PRINT
    for max_sheets, price in SOFT_BINDING_TIERS:
        if sheets <= max_sheets:
            return price
    return SOFT_BINDING_TIERS[-1][1]


def get_thermal_binding_rate(sheets: int) -> int:
    """Look up thermal/spiral sheet binding rate."""
    for max_sheets, price in THERMAL_BINDING_TIERS:
        if sheets <= max_sheets:
            return price
    return THERMAL_BINDING_TIERS[-1][1]


def get_print_rate(paper_type: str, sides: str, sheets: int,
                   is_student: bool = False) -> float:
    """
    Get per-sheet print rate based on paper type, sides, sheet count and
    student status.

    paper_type: 'A4_BW' | 'A4_col' | 'A4_bond_col' | 'Legal_BW' | 'A3_BW' | etc.
    sides:      'ss' | 'ds'
    sheets:     total sheet count (used for colour tier selection)
    is_student: apply student discount (B&W only)
    """
    sides = sides if sides in ("ss", "ds") else "ss"

    # A4 B&W — student rate override
    if paper_type == "A4_BW" and is_student:
        key = "A4_BW_student_100" if sheets <= 100 else "A4_BW_student_100p"
        return PRINT_RATES[key].get(sides, PRINT_RATES[key]["ss"])

    # A4 Colour — tiered by sheet count
    if paper_type == "A4_col":
        if sheets <= 30:
            key = "A4_col_30"
        elif sheets <= 50:
            key = "A4_col_50"
        else:
            key = "A4_col_50p"
        return PRINT_RATES[key].get(sides, PRINT_RATES[key]["ss"])

    # All other types — flat rate
    rate_dict = PRINT_RATES.get(paper_type, PRINT_RATES["A4_BW"])
    return rate_dict.get(sides, rate_dict.get("ss", 3.0))


def calculate_item_cost(pages: int, paper_type: str, sides: str,
                        layout: str, copies: int,
                        is_student: bool = False) -> dict:
    """
    Calculate print cost for a single print item (one line in a mixed job).

    Returns:
        { sheets, rate, print_cost, breakdown_line }
    """
    sheets = calc_sheets(pages, sides, layout)
    rate   = get_print_rate(paper_type, sides, sheets, is_student)
    cost   = round(sheets * copies * rate, 2)

    sides_label  = "SS" if sides == "ss" else "DS"
    colour_label = "Colour" if "col" in paper_type.lower() else "B&W"
    breakdown    = (f"{colour_label} {layout} {sides_label} - "
                    f"{sheets} sheets x {copies}x @ Rs.{rate} = Rs.{cost:.2f}")

    return {"sheets": sheets, "rate": rate, "print_cost": cost,
            "breakdown_line": breakdown}


def calculate_finishing_cost(finishing: str, sheets: int,
                             paper_size: str = "A4",
                             urgent: bool = False,
                             with_print: bool = True,
                             project_cover: str = "white") -> dict:
    """
    Calculate finishing cost.

    Returns:
        { finishing_cost, label, outsourced, breakdown_line }
    """
    finishing = finishing.lower().strip()
    cost = 0
    outsourced = finishing in FINISHING_OUTSOURCED
    label = FINISHING_DISPLAY.get(finishing, finishing)

    if finishing in ("none", "staple"):
        cost = 0
    elif finishing == "spiral":
        cost = get_spiral_rate(sheets, paper_size)
    elif finishing == "wiro":
        cost = get_spiral_rate(sheets, paper_size)  # same tiers as spiral
    elif finishing == "soft":
        cost = get_soft_binding_rate(sheets, with_print)
    elif finishing == "project":
        cost = PROJECT_BINDING_RATES.get(project_cover.lower(), 220)
    elif finishing == "record":
        cost = BINDING_RATES["record"]["price"]
    elif finishing == "lam_sheet":
        cost = LAMINATION_RATES["a4"]
    elif finishing == "thermal":
        cost = get_thermal_binding_rate(sheets)

    # Urgent surcharge
    surcharge = 0
    if urgent and finishing in URGENT_ELIGIBLE:
        surcharge = URGENT_SURCHARGE
        cost += surcharge

    breakdown = f"{label}: Rs.{cost:.0f}"
    if surcharge:
        breakdown += f" (incl. urgent +Rs.{surcharge})"
    if outsourced:
        breakdown += " [outsourced]"

    return {"finishing_cost": cost, "label": label,
            "outsourced": outsourced, "breakdown_line": breakdown}


def calculate_quote(print_items: list, finishing: str = "none",
                    urgent: bool = False, is_student: bool = False,
                    paper_size: str = "A4",
                    project_cover: str = "white",
                    with_print: bool = True) -> dict:
    """
    Master quote calculator for a full job with one or more print items.

    print_items: list of dicts:
        [{ "pages": int, "paper_type": str, "sides": str,
           "layout": str, "copies": int }, ...]

    Returns:
        {
            total_sheets: int,
            print_cost:   float,
            finishing_cost: float,
            total:        float,
            breakdown:    [str],   # list of human-readable lines
            outsourced_finishing: bool,
        }
    """
    total_sheets = 0
    print_cost   = 0.0
    breakdown    = []

    for i, item in enumerate(print_items, 1):
        pages      = int(item.get("pages", 1))
        ptype      = item.get("paper_type", "A4_BW")
        sides      = item.get("sides", "ss")
        layout     = item.get("layout", "1-up")
        copies     = int(item.get("copies", 1))

        r = calculate_item_cost(pages, ptype, sides, layout, copies, is_student)
        total_sheets += r["sheets"]
        print_cost   += r["print_cost"]
        prefix = f"Item {i}: " if len(print_items) > 1 else ""
        breakdown.append(prefix + r["breakdown_line"])

    # Finishing (calculated on total sheets across all items)
    fin = calculate_finishing_cost(
        finishing, total_sheets, paper_size, urgent, with_print, project_cover
    )
    finishing_cost = fin["finishing_cost"]
    if fin["breakdown_line"]:
        breakdown.append(fin["breakdown_line"])

    total = round(print_cost + finishing_cost, 2)
    breakdown.append(f"--- Total: Rs.{total:.2f}")

    return {
        "total_sheets":        total_sheets,
        "print_cost":          round(print_cost, 2),
        "finishing_cost":      finishing_cost,
        "total":               total,
        "breakdown":           breakdown,
        "outsourced_finishing": fin["outsourced"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7 — BACKWARD-COMPAT FUNCTIONS (kept for existing watcher/bot code)
# ─────────────────────────────────────────────────────────────────────────────

def calculate_sheets(page_count: int, layout: str, sided: str) -> int:
    """
    Legacy function — kept for backward compatibility with watcher.py and bot.
    Translates old 'single'/'double'/'2up' layout to new calc_sheets().
    """
    # Map old layout names
    sides_new  = "ds" if layout == "double" else "ss"
    layout_new = "1-up"
    if layout in ("2up",):
        layout_new = "2-up"
        sides_new  = "ds" if sided == "double" else "ss"
    elif layout in ("4up",):
        layout_new = "4-up"
        sides_new  = "ds" if sided == "double" else "ss"
    elif layout == "double":
        layout_new = "1-up"
        sides_new  = "ds"
    return calc_sheets(page_count, sides_new, layout_new)


def calculate_print_cost(
    page_count: int,
    size: str,
    colour: str,
    layout: str,
    sided: str,
    copies: int,
    finishing: str,
    delivery: bool,
) -> dict:
    """
    Legacy function — kept for backward compatibility.
    Maps old parameters to new calculate_quote() and returns same dict shape.
    """
    # Map old paper_type from size + colour
    paper_type = f"{size.upper()}_{'col' if colour == 'col' else 'BW'}"
    sides_new  = "ds" if layout == "double" else "ss"
    layout_new = "1-up"
    if layout in ("2up",):
        layout_new = "2-up"
    elif layout in ("4up",):
        layout_new = "4-up"

    print_items = [{"pages": page_count, "paper_type": paper_type,
                    "sides": sides_new, "layout": layout_new, "copies": copies}]
    result = calculate_quote(print_items, finishing=finishing,
                             paper_size=size.upper())

    # Map to legacy return shape
    fin = FINISHING_RATES.get(finishing, FINISHING_RATES["none"])
    delivery_cost = DELIVERY_CHARGE if delivery else 0
    total = result["total"] + delivery_cost

    layout_label = {"single":"Single side","double":"Double side",
                    "2up":"2-up","4up":"4-up"}.get(layout, layout)
    colour_label = "B&W" if colour == "bw" else "Colour"

    return {
        "sheets":             result["total_sheets"],
        "print_cost":         result["print_cost"],
        "finishing_cost":     result["finishing_cost"],
        "delivery_cost":      delivery_cost,
        "total":              total,
        "staff_quote_needed": fin.get("staff_quote", False),
        "finishing_label":    fin.get("label", finishing),
        "breakdown":          "\n".join(result["breakdown"]),
    }


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8 — SUPABASE RATE LOADING (existing — kept intact)
# ─────────────────────────────────────────────────────────────────────────────

_KEY_MAP = {
    "a4_bw_single":      ("RATES", "A4", "bw",  "single"),
    "a4_bw_double":      ("RATES", "A4", "bw",  "double"),
    "a4_col_single":     ("RATES", "A4", "col", "single"),
    "a4_col_double":     ("RATES", "A4", "col", "double"),
    "a3_bw_single":      ("RATES", "A3", "bw",  "single"),
    "a3_col_single":     ("RATES", "A3", "col", "single"),
    "finishing_staple":  ("FINISHING", "staple"),
    "finishing_spiral":  ("FINISHING", "spiral"),
    "finishing_wiro":    ("FINISHING", "wiro"),
    "finishing_soft":    ("FINISHING", "soft"),
    "finishing_project": ("FINISHING", "project"),
    "finishing_record":  ("FINISHING", "record"),
    "delivery":          ("DELIVERY",),
}


def load_rates_from_supabase(supabase_url: str, supabase_key: str) -> bool:
    """
    Fetch rate_card table from Supabase and update live RATES/FINISHING_RATES.
    Returns True if successful, False if fallback used.
    Called once at watcher startup.
    """
    global RATES, FINISHING_RATES, DELIVERY_CHARGE
    try:
        import urllib.request
        import json
        url = f"{supabase_url}/rest/v1/rate_card?select=key,price,staff_quote"
        req = urllib.request.Request(url, headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            rows = json.loads(resp.read())

        if not rows:
            return False

        for row in rows:
            key     = row.get("key", "")
            price   = float(row.get("price", 0))
            staff   = bool(row.get("staff_quote", False))
            mapping = _KEY_MAP.get(key)
            if not mapping:
                continue
            if mapping[0] == "RATES":
                _, size, col, side = mapping
                RATES[size][col][side] = price
            elif mapping[0] == "FINISHING":
                _, fin_key = mapping
                if fin_key in FINISHING_RATES:
                    FINISHING_RATES[fin_key]["price"]       = price
                    FINISHING_RATES[fin_key]["staff_quote"] = staff
            elif mapping[0] == "DELIVERY":
                DELIVERY_CHARGE = price

        return True
    except Exception as e:
        logging.warning("rate_card: Supabase load failed (%s) — using hardcoded defaults", e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 9 — PDF UTILITIES (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def get_pdf_page_count(filepath: str) -> int:
    """Extract page count from a PDF file. Tries pikepdf → pypdf → PyPDF2."""
    try:
        import pikepdf
        with pikepdf.open(filepath) as _pdf:
            return len(_pdf.pages)
    except Exception:
        pass
    try:
        import pypdf
        with open(filepath, "rb") as f:
            return len(pypdf.PdfReader(f).pages)
    except Exception:
        pass
    try:
        import PyPDF2
        with open(filepath, "rb") as f:
            return len(PyPDF2.PdfReader(f).pages)
    except Exception:
        pass
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 10 — SELF-TEST (run: python rate_card.py)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== rate_card.py self-test ===\n")

    tests = [
        # (desc, pages, sides, layout, expected_sheets)
        ("34p DS 1-up",   34, "ds", "1-up", 18),   # ceil(34/2)=17 → next even=18
        ("5p  DS 1-up",    5, "ds", "1-up",  4),   # ceil(5/2)=3 → next even=4
        ("6p  DS 1-up",    6, "ds", "1-up",  4),   # ceil(6/2)=3 → next even=4
        ("50p SS 2-up",   50, "ss", "2-up", 25),   # ceil(50/2)=25 sheets
        ("10p SS 1-up",   10, "ss", "1-up", 10),
        ("1p  DS 1-up",    1, "ds", "1-up",  2),   # ceil(1/2)=1 → next even=2
    ]

    all_pass = True
    for desc, pages, sides, layout, expected in tests:
        got = calc_sheets(pages, sides, layout)
        status = "PASS" if got == expected else "FAIL"
        if got != expected:
            all_pass = False
        print(f"  [{status}] calc_sheets({pages}p, {sides}, {layout}) = {got}  (expected {expected})")

    print()
    # Full quote test
    q = calculate_quote(
        print_items=[{"pages": 34, "paper_type": "A4_BW",
                      "sides": "ds", "layout": "1-up", "copies": 1}],
        finishing="spiral"
    )
    print(f"  Quote: 34p A4 B&W DS 1-up Spiral = Rs.{q['total']}")
    print(f"  Breakdown: {q['breakdown']}")
    # Expected: 18 sheets x Rs.3 = Rs.54 print + Rs.30 spiral = Rs.84

    print()
    # Mixed job test
    q2 = calculate_quote(
        print_items=[
            {"pages": 5,  "paper_type": "A4_col", "sides": "ss", "layout": "1-up", "copies": 1},
            {"pages": 45, "paper_type": "A4_BW",  "sides": "ds", "layout": "1-up", "copies": 1},
        ],
        finishing="none"
    )
    print(f"  Mixed job: 5 col SS + 45 BW DS = Rs.{q2['total']}")
    print(f"  Breakdown: {q2['breakdown']}")

    print()
    print("All sheet tests passed!" if all_pass else "⚠ Some sheet tests FAILED — check logic.")
