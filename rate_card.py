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
  Sheets for DS = ceil(pages/2).  (Until 6afb9b5, 2026-08-14, this rounded up
  to the next even number; that rounding was a bug and was removed. This line
  documented the old behaviour for two weeks after it stopped being true.)
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

    # A5 -- half the A4 rate, flat. No student discount (owner, 2026-08-30).
    "A5_BW":              {"ss": 1.5, "ds": 1.5},
    "A5_col_30":          {"ss": 5.0,  "ds": 10.0},   # <=30 sheets
    "A5_col_50":          {"ss": 4.5,  "ds": 9.0},    # 31-50
    "A5_col_50p":         {"ss": 4.0,  "ds": 8.0},    # >50

    # Letter -- the A4 rate. No student discount (owner, 2026-08-30).
    "Letter_BW":          {"ss": 3.0, "ds": 3.0},
    "Letter_col_30":      {"ss": 10.0, "ds": 20.0},
    "Letter_col_50":      {"ss": 9.0,  "ds": 18.0},
    "Letter_col_50p":     {"ss": 8.0,  "ds": 16.0},

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

# A3 spiral: the A4 tiers scaled by the ratio the two start rates already
# implied (80/30 = 2.67), rounded to the nearest Rs.10 (owner, 2026-08-30).
# Until then A3 was a flat Rs.80 at every thickness, so a 250-sheet A3 spiral
# was the cheapest binding in the shop.
SPIRAL_A3_TIERS = [
    (30,  80),
    (70,  110),
    (100, 130),
    (130, 160),
    (150, 210),
    (170, 240),
    (200, 320),
    (250, 400),
]
SPIRAL_A3_START = SPIRAL_A3_TIERS[0][1]  # kept: the entry rate, now tier one

# Soft binding (with print) — tiered by sheet count
SOFT_BINDING_TIERS = [
    (70,  80),
    (100, 110),
    (130, 120),
    (150, 140),
    (200, 160),
    (250, 180),
]
# Binding a customer's own sheets costs Rs.20 more than binding what we printed
# (owner, 2026-08-30). This was already in the code without being named: the old
# SOFT_BINDING_WITHOUT_PRINT of 100 is exactly soft's Rs.80 tier plus this 20.
# Applies to the bindings the owner was asked about -- spiral, wiro, soft and
# perfect. Project, record and thesis have their own bind-only prices.
BIND_ONLY_PREMIUM = 20
BIND_ONLY_PREMIUM_APPLIES = {"spiral", "wiro", "soft", "perfect"}
SOFT_BINDING_WITHOUT_PRINT = 100  # = SOFT_BINDING_TIERS[0][1] + BIND_ONLY_PREMIUM

# Perfect binding is priced as soft binding (owner, 2026-08-30); the "+20 for
# binding only" is the general rule above, not a perfect-specific premium.
PERFECT_USES_SOFT_TIERS = True

# Thesis: a flat binding line when we print it, printing charged per sheet on
# top as normal. When the customer brings their own sheets it is the project
# rate plus a premium (owner, 2026-08-30).
THESIS_WITH_PRINT = 500
THESIS_BIND_ONLY_PREMIUM = 100

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

# Wiro binding: Rs.50 at the first tier, Rs.50 more at each one after, and the
# machine stops at 150 sheets (owner, 2026-08-30). Until then wiro borrowed
# spiral's tiers with a "for now" that nobody came back to.
WIRO_A4_TIERS = [
    (30,  50),
    (70,  100),
    (100, 150),
    (130, 200),
    (150, 250),
]
WIRO_MAX_SHEETS = 150

BINDING_RATES = {
    "none":     {"price": 0,   "label": "No binding",         "outsourced": False},
    "staple":   {"price": 0,   "label": "Staple",             "outsourced": False},
    "spiral":   {"price": None,"label": "Spiral binding",     "outsourced": False, "tiered": True},
    "wiro":     {"price": None,"label": "Wiro binding",       "outsourced": False, "tiered": True},
    "soft":     {"price": None,"label": "Soft binding",       "outsourced": False, "tiered": True},
    "perfect":  {"price": None,"label": "Perfect binding",    "outsourced": False, "tiered": True},
    "project":  {"price": None,"label": "Project binding",    "outsourced": True,  "tiered": False},
    "thesis":   {"price": None,"label": "Thesis binding",     "outsourced": True,  "tiered": False},
    "record":   {"price": 400, "label": "Record binding (A3)","outsourced": True,  "tiered": False},
    "lam_sheet":{"price": None,"label": "Pouch lamination",   "outsourced": False, "tiered": False},
    "lam_roll": {"price": None,"label": "Roll lamination",    "outsourced": True,  "tiered": False},
    "lam_cover":{"price": 50,  "label": "Cover lamination",   "outsourced": True,  "tiered": False},
    "id_card":  {"price": 100, "label": "ID card printing",   "outsourced": False, "tiered": False},
}

# Every key above must be priced by calculate_finishing_cost. ZERO_PRICED names
# the two that are legitimately free; anything else reaching cost 0 is a bug,
# and calculate_finishing_cost flags it rather than quietly charging nothing.
# tests/test_rate_card.py::TestNoUnpricedFinishing holds this in place.
ZERO_PRICED_FINISHINGS = {"none", "staple"}

URGENT_SURCHARGE = 20
# Any finishing or service can be rushed (owner, 2026-08-30). Was soft +
# project only; an operator should not have to remember which things can be
# urgent and which cannot.
URGENT_ELIGIBLE  = set(BINDING_RATES) - ZERO_PRICED_FINISHINGS

# Pouch / sheet lamination, by size. The a4/a3 figures carry the owner's
# 2026-08-30 premium (+Rs.10 up to A4, +Rs.20 for A3) on the rates that were
# already here. Until then calculate_finishing_cost hardcoded LAMINATION_RATES
# ["a4"], so an A3 pouch lamination billed as A4 and the A3 numbers were dead.
LAMINATION_RATES = {
    "normal":    40,
    "with_col":  50,   # with colour copy (Aadhar, RC, licence)
    "a4":        70,
    "a3_bw":     120,
    "a3_col":    140,
}

# Roll lamination is a DIFFERENT process from pouch at a different price --
# per sheet, minimum 10 sheets (owner, 2026-08-30). Wiring it to
# LAMINATION_RATES would overcharge by roughly 4x.
ROLL_LAM_RATES = {"A4": 15, "A3": 30}
ROLL_LAM_MIN_SHEETS = 10

# Thermal binding was withdrawn on 2026-08-30 -- no longer offered, in either
# store or on the order page. Removed rather than re-tested (backlog S7-5 had
# it listed as "rate never tested" since March). Its tiers were (50, 60) and
# (100, 80) if it is ever revived.

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — OTHER SERVICE RATES
# ─────────────────────────────────────────────────────────────────────────────

# Scanning, per sheet, banded by how many sheets. A3 is double A4 throughout
# (owner, 2026-08-30).
#
# The old "special": 2 entry — a per-customer rate for Sini/Ujjwala — was
# removed the same day. A per-customer override living in a shared table is one
# that gets applied by accident; if it comes back it belongs on the customer,
# not here.
SCANNING_TIERS = {
    "A4": [(50, 10), (100, 7), (None, 5)],
    "A3": [(50, 20), (100, 14), (None, 10)],
}
SCANNING_RATES = {            # kept: existing callers read these three keys
    "standard_50":  10,
    "standard_100": 7,
    "standard_100p": 5,
}

# Typing only — printing the typed pages is charged at the ordinary print rates
# on top (owner, 2026-08-30). Per page.
DTP_RATES = {
    "malayalam": 40,
    "english":   40,
    "hindi":     60,
}

# Foiling, per sheet or piece, with a 10-piece minimum (owner, 2026-08-30).
# A cover is always larger than A3 and takes the "cover" rate — which is why
# the owner's "minimum Rs.500 for up to 10 covers" needs no special case:
# 10 x 50 IS 500, so one max(pieces, minimum) rule prices all three.
FOILING_RATES = {"A4": 30, "A3": 50, "cover": 50}

# Cutting and punching, per PASS of the machine — one press, however many
# sheets it takes at a time — with a floor per job. Free when the job is one we
# printed or bound; the rate is for a customer's own sheets (owner, 2026-08-30).
HANDWORK_RATES = {"cut": 20, "punch": 20}
HANDWORK_MIN_CHARGE = {"cut": 100, "punch": 100}

# Minimum billable quantity, by service. Under it, the minimum is what bills.
MIN_PIECES = {"foil": 10, "lam_roll": ROLL_LAM_MIN_SHEETS}

# Photos are printed from a soft copy the customer supplies — Printosky does
# not shoot them (owner, 2026-08-30). Stamp, postcard and 4x6 are offered but
# their rates have not been given yet, so they price manually rather than
# guess.
PHOTO_RATES = {"set5": 50, "sheet": 100}
PHOTO_SIZES_PENDING_RATES = ("stamp", "postcard", "4x6")

DELIVERY_CHARGE = 30

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — FINISHING TYPE METADATA (for UI dropdowns)
# ─────────────────────────────────────────────────────────────────────────────

# NOTE (2026-08-30): these two lists are a whole-company answer to a per-store
# question. Binding, roll lamination and foiling are done in house at Nattika
# (PRINTK) and outsourced everywhere else, so "outsourced" is a property of the
# store, not of the finishing. Backlog S13-12 replaces this with
# is_outsourced(finishing, store_id) driven by store_config.json capabilities;
# until then these stay as the no-store-context fallback.
FINISHING_INHOUSE    = ["none", "staple", "spiral", "wiro", "perfect",
                        "lam_sheet", "id_card"]
FINISHING_OUTSOURCED = ["lam_roll", "lam_cover", "project", "record", "thesis"]

#: Which store capability would bring an outsourced finishing in-house.
#: Only the keys in FINISHING_OUTSOURCED appear here: a finishing we already do
#: ourselves everywhere (spiral, wiro, staple, pouch lamination, ID cards) is
#: not capability-gated, because making it one would let a store lose work it
#: has always done by not listing a capability.
FINISHING_CAPABILITY = {
    "lam_roll":  "roll_lam",
    "lam_cover": "roll_lam",   # same roll machine
    "project":   "binding",
    "record":    "binding",
    "thesis":    "binding",
}
FINISHING_URGENT_OK  = sorted(URGENT_ELIGIBLE)  # sorted: reaches the UI

FINISHING_DISPLAY = {
    "none":     "No Finishing",
    "staple":   "Staple",
    "spiral":   "Spiral Binding",
    "wiro":     "Wiro Binding",
    "soft":     "Soft Binding",
    "perfect":  "Perfect Binding",
    "thesis":   "Thesis Binding",
    "project":  "Project Binding",
    "record":   "Record Binding",
    "lam_sheet":"Pouch Lamination",
    "lam_roll": "Roll Lamination",
    "lam_cover":"Cover Lamination",
    "id_card":  "ID Card Printing",
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
    "perfect": {"price": 80,  "label": "Perfect binding","staff_quote": True},
    "thesis":  {"price": 500, "label": "Thesis binding", "staff_quote": False},
    "record":  {"price": 400, "label": "Record binding", "staff_quote": False},
    "lam_sheet":{"price":70,  "label": "Pouch lam",      "staff_quote": False},
    "lam_roll":{"price": None,"label": "Roll lam",       "staff_quote": True},
    "lam_cover":{"price":50,  "label": "Cover lam",      "staff_quote": False},
    "id_card": {"price": 100, "label": "ID card",        "staff_quote": False},
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
    else:
        sheets = pages

    return max(1, sheets)


def _tier_price(tiers: list, sheets: int) -> int:
    """First tier whose ceiling the sheet count fits under; the last one caps."""
    for max_sheets, price in tiers:
        if sheets <= max_sheets:
            return price
    return tiers[-1][1]


def get_spiral_rate(sheets: int, size: str = "A4") -> int:
    """Spiral binding rate for a sheet count and paper size. A3 is tiered too
    (it used to be a flat Rs.80 however thick the job was)."""
    tiers = SPIRAL_A3_TIERS if size.upper() == "A3" else SPIRAL_A4_TIERS
    return _tier_price(tiers, sheets)


def get_wiro_rate(sheets: int) -> int | None:
    """Wiro binding rate, or None above WIRO_MAX_SHEETS -- the machine cannot
    take it, so the counter offers spiral or soft instead. None means refuse,
    never charge zero."""
    if sheets > WIRO_MAX_SHEETS:
        return None
    return _tier_price(WIRO_A4_TIERS, sheets)


def get_roll_lam_cost(sheets: int, size: str = "A4") -> int:
    """Roll lamination: per sheet, billed at a minimum of 10 sheets."""
    rate = ROLL_LAM_RATES.get(size.upper(), ROLL_LAM_RATES["A4"])
    return max(sheets, ROLL_LAM_MIN_SHEETS) * rate


def get_pouch_lam_rate(size: str = "A4", is_colour: bool = False) -> int:
    """Pouch/sheet lamination, by paper size. Anything at or below A4 takes the
    A4 rate; A3 has its own, and a colour A3 costs more than a mono one."""
    if size.upper() == "A3":
        return LAMINATION_RATES["a3_col"] if is_colour else LAMINATION_RATES["a3_bw"]
    return LAMINATION_RATES["a4"]


def get_soft_binding_rate(sheets: int, with_print: bool = True) -> int:
    """Soft binding rate for a sheet count.

    Without printing, the general bind-only premium applies -- which is what
    the old flat SOFT_BINDING_WITHOUT_PRINT of 100 was all along, for the first
    tier. It now scales with thickness instead of collapsing every job to 100.
    """
    price = _tier_price(SOFT_BINDING_TIERS, sheets)
    return price + (0 if with_print else BIND_ONLY_PREMIUM)


def _colour_tier_key(size: str, sheets: int) -> str:
    """Colour rates are banded by total sheet count, identically for every size
    that has colour bands (A4, A5, Letter)."""
    band = "30" if sheets <= 30 else ("50" if sheets <= 50 else "50p")
    return f"{size}_col_{band}"


# Sizes whose colour rate is banded by sheet count. Legal and A3 are flat.
TIERED_COLOUR_SIZES = ("A4", "A5", "Letter")

# The student discount is A4 B&W only, deliberately: A5 and Letter are already
# priced without one (owner, 2026-08-30: "no discounts"). Do not widen this
# without asking -- A5 at Rs.1.50 is already the heavy-volume student A4 rate.
STUDENT_DISCOUNT_TYPES = ("A4_BW",)


def get_print_rate(paper_type: str, sides: str, sheets: int,
                   is_student: bool = False) -> float:
    """
    Get per-sheet print rate based on paper type, sides, sheet count and
    student status.

    paper_type: 'A4_BW' | 'A4_col' | 'A5_BW' | 'Letter_col' | 'A3_BW' | etc.
    sides:      'ss' | 'ds'
    sheets:     total sheet count (used for colour tier selection)
    is_student: apply student discount (A4 B&W only)

    Every size the order page offers must resolve to a real rate here. It did
    not until 2026-08-30: A5 and Letter had no entries, so the fallback at the
    bottom billed them -- colour included -- at A4 B&W. tests/test_rate_card.py
    ::TestEverySizeHasARate holds that shut.
    """
    sides = sides if sides in ("ss", "ds") else "ss"

    # A4 B&W — student rate override
    if paper_type in STUDENT_DISCOUNT_TYPES and is_student:
        key = "A4_BW_student_100" if sheets <= 100 else "A4_BW_student_100p"
        return PRINT_RATES[key].get(sides, PRINT_RATES[key]["ss"])

    # Banded colour — A4, A5, Letter
    if paper_type.endswith("_col"):
        size = paper_type[:-len("_col")]
        if size in TIERED_COLOUR_SIZES:
            key = _colour_tier_key(size, sheets)
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
    is_colour = "col" in paper_type.lower()
    # Colour is billed strictly per page (owner rule): duplex never changes a
    # colour job's price and there is no doubled DS rate. Force single-sided
    # billing so the billed quantity = pages (after any N-up) at the base
    # per-page rate. B&W keeps the per-sheet model (duplex still ~halves it).
    bill_sides = "ss" if is_colour else sides
    sheets = calc_sheets(pages, bill_sides, layout)
    rate   = get_print_rate(paper_type, bill_sides, sheets, is_student)
    cost   = round(sheets * copies * rate, 2)

    sides_label  = "SS" if sides == "ss" else "DS"
    colour_label = "Colour" if is_colour else "B&W"
    unit         = "pages" if is_colour else "sheets"
    breakdown    = (f"{colour_label} {layout} {sides_label} - "
                    f"{sheets} {unit} x {copies}x @ Rs.{rate} = Rs.{cost:.2f}")

    return {"sheets": sheets, "rate": rate, "print_cost": cost,
            "breakdown_line": breakdown}



def is_outsourced(finishing: str, store_id: str | None = None,
                  capabilities: dict | None = None) -> bool:
    """Does `finishing` have to leave this store?

    The default answer is the one FINISHING_OUTSOURCED has always given, so a
    caller with no store context — every caller before 2026-09-01 — gets exactly
    today's behaviour.

    With a store's capabilities in hand, a store that owns the machine keeps the
    work: PRINTK (Nattika) binds and laminates on the roll, so a record binding
    booked there is in-house, while the same job at OSP still goes out.

    **Absent or false means outsourced** (plan §4.7, decision B9). A new store
    claims nothing until someone writes the claim down, because the claim is
    what decides whether a customer is promised today or next week.

    `capabilities` is for callers that already hold a store's config; otherwise
    the active store's config is read, and only when `store_id` names it — asking
    about another store without passing its capabilities cannot be answered from
    here, so it falls back to the default rather than guessing.
    """
    key = (finishing or "").strip().lower()
    if key not in FINISHING_OUTSOURCED:
        return False                      # never outsourced, whatever the store

    caps = capabilities
    if caps is None:
        caps = _active_store_capabilities(store_id)
    if not caps:
        return True                       # nothing claimed -> outsourced

    needed = FINISHING_CAPABILITY.get(key)
    if needed is None:
        return True                       # outsourced with no capability to own it
    return not bool(caps.get(needed, False))


def _active_store_capabilities(store_id: str | None) -> dict | None:
    """This machine's capabilities, but only if it is the store being asked about.

    Returns None when the question cannot be answered locally — a different
    store, or no config — so `is_outsourced` falls back to the safe default
    instead of answering with the wrong store's machines.
    """
    try:
        from store_config import get_store_config
        cfg = get_store_config()
    except Exception:
        return None
    if store_id and str(store_id).strip().upper() != cfg.store_id.upper():
        return None
    return dict(cfg.capabilities)


def calculate_finishing_cost(finishing: str, sheets: int,
                             paper_size: str = "A4",
                             urgent: bool = False,
                             with_print: bool = True,
                             project_cover: str = "white",
                             is_colour: bool = False) -> dict:
    """
    Calculate finishing cost.

    Returns:
        { finishing_cost, label, outsourced, breakdown_line, unpriced, refused }

    ``unpriced`` is the important one. Until 2026-08-30 this function had no
    branch for lam_roll, lam_cover or id_card, and no key at all for perfect or
    thesis -- all five fell through every branch and returned the zero this
    function initialises with, so a job finished with roll lamination was
    quoted the printing and nothing for the lamination. Now anything that
    reaches zero outside ZERO_PRICED_FINISHINGS comes back flagged, for the
    caller to alert on rather than silently charge nothing.

    ``refused`` means the shop cannot do it at this size -- wiro above 150
    sheets. Also never a silent zero.

    ``is_colour`` only affects A3 pouch lamination, which costs more in colour.
    """
    finishing = finishing.lower().strip()
    cost = 0
    outsourced = finishing in FINISHING_OUTSOURCED
    label = FINISHING_DISPLAY.get(finishing, finishing)
    unpriced = False
    refused = ""

    if finishing in ZERO_PRICED_FINISHINGS:
        cost = 0
    elif finishing == "spiral":
        cost = get_spiral_rate(sheets, paper_size)
    elif finishing == "wiro":
        rate = get_wiro_rate(sheets)
        if rate is None:
            refused = (f"wiro binding stops at {WIRO_MAX_SHEETS} sheets "
                       f"({sheets} asked for) — offer spiral or soft instead")
        else:
            cost = rate
    elif finishing in ("soft", "perfect"):
        # Perfect is priced as soft (owner, 2026-08-30).
        cost = get_soft_binding_rate(sheets, with_print)
    elif finishing == "project":
        cost = PROJECT_BINDING_RATES.get(project_cover.lower(), 220)
    elif finishing == "thesis":
        # A flat binding line when we print it; the customer's own sheets cost
        # the project rate plus a premium.
        cost = (THESIS_WITH_PRINT if with_print else
                PROJECT_BINDING_RATES.get(project_cover.lower(), 220)
                + THESIS_BIND_ONLY_PREMIUM)
    elif finishing == "record":
        cost = BINDING_RATES["record"]["price"]
    elif finishing == "lam_sheet":
        cost = get_pouch_lam_rate(paper_size, is_colour)
    elif finishing == "lam_roll":
        cost = get_roll_lam_cost(sheets, paper_size)
    elif finishing == "lam_cover":
        cost = BINDING_RATES["lam_cover"]["price"]
    elif finishing == "id_card":
        # Per card, printing included -- `sheets` is the card count here.
        cost = BINDING_RATES["id_card"]["price"] * max(1, sheets)
    else:
        unpriced = True

    # The bind-only premium, for the bindings it applies to. Soft AND perfect
    # both price through get_soft_binding_rate, which already adds it, so
    # adding it again here would charge them twice.
    _ALREADY_PREMIUMED = {"soft", "perfect"}
    if (not with_print and finishing in BIND_ONLY_PREMIUM_APPLIES
            and finishing not in _ALREADY_PREMIUMED and not refused):
        cost += BIND_ONLY_PREMIUM

    if cost == 0 and finishing not in ZERO_PRICED_FINISHINGS and not refused:
        unpriced = True

    # Urgent surcharge — any finishing, not just soft and project.
    surcharge = 0
    if urgent and finishing in URGENT_ELIGIBLE and not refused:
        surcharge = URGENT_SURCHARGE
        cost += surcharge

    if refused:
        breakdown = f"{label}: NOT POSSIBLE — {refused}"
    else:
        breakdown = f"{label}: Rs.{cost:.0f}"
        if not with_print and finishing in BIND_ONLY_PREMIUM_APPLIES:
            breakdown += " (binding only, incl. +Rs.20)"
        if finishing == "lam_roll" and sheets < ROLL_LAM_MIN_SHEETS:
            breakdown += (f" (minimum {ROLL_LAM_MIN_SHEETS} sheets applied, "
                          f"{sheets} brought)")
        if surcharge:
            breakdown += f" (incl. urgent +Rs.{surcharge})"
        if outsourced:
            breakdown += " [outsourced]"
    if unpriced:
        breakdown = f"{label}: NO RATE — quote manually"

    return {"finishing_cost": cost, "label": label,
            "outsourced": outsourced, "breakdown_line": breakdown,
            "unpriced": unpriced, "refused": refused}


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
        finishing, total_sheets, paper_size, urgent, with_print, project_cover,
        is_colour=any("col" in str(i.get("paper_type", "")).lower()
                      for i in print_items),
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
        # Callers must surface these rather than bill the total silently.
        "unpriced_finishing":  fin["unpriced"],
        "refused_finishing":   fin["refused"],
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
# SECTION 11 — POST-PRESS SERVICES (work with no printing)
#
# Copy, scan, laminate, foil, bind, cut, punch, photo, DTP — the things a
# customer brings in that never touch a printer, plus photocopying which does.
# Rates and rules are the owner's, given 2026-08-30; the plan and its reasoning
# are in docs/plans/2026-08-30-scaling-and-post-press-services.md.
#
# Nothing calls this yet. It is wired up in B-3 (/service-quote, /new-service).
#
# Two rules run through all of it:
#   * A minimum always names itself in the breakdown. An operator has to be able
#     to explain "why is 3 sheets Rs.300" before the customer asks.
#   * A service this cannot price comes back flagged, never as a Rs.0 line.
#     That is the whole lesson of the five finishings that billed zero.
# ─────────────────────────────────────────────────────────────────────────────

#: kind -> (label, whether the operator must type the price)
SERVICE_KINDS = {
    "copy":     ("Photocopy",          False),
    "scan":     ("Scanning",           False),
    "laminate": ("Lamination",         False),
    "foil":     ("Foiling",            False),
    "bind":     ("Binding only",       False),
    "cut":      ("Cutting / trimming", False),
    "punch":    ("Punching",           False),
    "photo":    ("Photo prints",       False),
    "dtp":      ("DTP / typing",       False),
    "other":    ("Other",              True),
}

# Any service can be rushed (owner, 2026-08-30) — not just the bindings that
# used to be the only urgent-eligible work.
SERVICE_URGENT_SURCHARGE = URGENT_SURCHARGE

# The student rate reaches photocopying and printing, and nothing else
# (owner, 2026-08-30).
STUDENT_RATE_KINDS = ("copy",)


def get_scan_rate(sheets: int, paper_size: str = "A4") -> int:
    """Per-sheet scan rate for a sheet count and size. A3 is double A4."""
    tiers = SCANNING_TIERS.get(paper_size.upper(), SCANNING_TIERS["A4"])
    for max_sheets, price in tiers:
        if max_sheets is None or sheets <= max_sheets:
            return price
    return tiers[-1][1]


def get_foiling_cost(pieces: int, paper_size: str = "A4") -> tuple[int, int]:
    """Foiling: (cost, billable pieces). Under the minimum, the minimum bills."""
    rate = FOILING_RATES.get(_foil_size_key(paper_size), FOILING_RATES["A4"])
    billable = max(int(pieces or 0), MIN_PIECES["foil"])
    return billable * rate, billable


def _foil_size_key(paper_size: str) -> str:
    """A cover is larger than A3 and has its own rate; everything else maps to
    the sheet sizes."""
    p = (paper_size or "A4").strip().lower()
    if p in ("cover", "covers"):
        return "cover"
    return "A3" if p == "a3" else "A4"


def get_handwork_cost(kind: str, passes: int, with_our_job: bool = False) -> int:
    """Cutting or punching: per machine pass, with a floor — and free when the
    job is one we printed or bound."""
    if with_our_job:
        return 0
    rate = HANDWORK_RATES[kind]
    return max(int(passes or 1) * rate, HANDWORK_MIN_CHARGE[kind])


def calculate_service_quote(kind: str, meta: dict | None = None) -> dict:
    """Price one post-press service.

    Returns:
        { total, breakdown[], label, needs_manual_price, unpriced }

    ``needs_manual_price`` means the shop does this but no rate is set for it
    (foiling on an unlisted size, a photo size whose rate has not been given,
    "other"). ``unpriced`` means the kind itself is unknown — a bug, not a
    business case. Either way the caller must surface it rather than bill the
    zero: see docs/FAIL_LOUD.md.
    """
    meta = meta or {}
    kind = (kind or "").strip().lower()

    if kind not in SERVICE_KINDS:
        return {"total": 0, "breakdown": [f"Unknown service {kind!r} — NO RATE"],
                "label": kind or "(none)", "needs_manual_price": True,
                "unpriced": True}

    label, manual_by_default = SERVICE_KINDS[kind]
    lines: list[str] = []
    total = 0.0
    needs_manual = manual_by_default

    sheets = max(0, int(meta.get("sheets") or 0))
    size = (meta.get("paper_size") or "A4").strip()

    if kind == "copy":
        # A photocopied sheet costs what a printed one costs — same machine,
        # same paper — so it prices through the print rate card, student rate
        # included.
        copies = max(1, int(meta.get("copies") or 1))
        colour = "col" if str(meta.get("colour", "bw")).lower() in ("col", "colour", "color") else "bw"
        sides = meta.get("sides", "ss")
        paper_type = f"{size.upper()}_{'col' if colour == 'col' else 'BW'}"
        r = calculate_item_cost(max(1, sheets), paper_type, sides, "1-up", copies,
                                bool(meta.get("is_student")))
        total = r["print_cost"]
        lines.append(r["breakdown_line"])

    elif kind == "scan":
        rate = get_scan_rate(sheets, size)
        total = sheets * rate
        lines.append(f"Scan {size.upper()}: {sheets} sheets x Rs.{rate} = Rs.{total:.0f}")

    elif kind == "laminate":
        lam_type = str(meta.get("lam_type", "pouch")).strip().lower()
        qty = max(1, sheets)
        if lam_type == "roll":
            total = get_roll_lam_cost(qty, size)
            lines.append(_min_line("Roll lamination", size, qty,
                                   MIN_PIECES["lam_roll"],
                                   ROLL_LAM_RATES.get(size.upper(), ROLL_LAM_RATES["A4"]),
                                   total))
        elif lam_type == "cover":
            rate = BINDING_RATES["lam_cover"]["price"]
            total = qty * rate
            lines.append(f"Cover lamination: {qty} x Rs.{rate} = Rs.{total:.0f}")
        elif lam_type == "id":
            # ID-card lamination is a different product from ID card PRINTING
            # (Rs.100/card, printing included) and has no rate of its own yet.
            needs_manual = True
            lines.append("ID lamination: NO RATE — enter the price")
        else:
            rate = get_pouch_lam_rate(size, bool(meta.get("is_colour")))
            total = qty * rate
            lines.append(f"Pouch lamination {size.upper()}: {qty} x Rs.{rate} = Rs.{total:.0f}")

    elif kind == "foil":
        key = _foil_size_key(size)
        if key not in FOILING_RATES:
            needs_manual = True
            lines.append(f"Foiling {size}: NO RATE — enter the price")
        else:
            total, billable = get_foiling_cost(sheets, size)
            lines.append(_min_line("Foiling", key, sheets, MIN_PIECES["foil"],
                                   FOILING_RATES[key], total))

    elif kind == "bind":
        fin = calculate_finishing_cost(
            str(meta.get("binding", "none")), max(1, sheets), size,
            urgent=False, with_print=False,
            project_cover=str(meta.get("project_cover", "white")))
        total = fin["finishing_cost"]
        lines.append(fin["breakdown_line"])
        needs_manual = needs_manual or fin["unpriced"] or bool(fin["refused"])

    elif kind in ("cut", "punch"):
        passes = max(1, int(meta.get("passes") or 1))
        free = bool(meta.get("with_our_job"))
        total = get_handwork_cost(kind, passes, free)
        if free:
            lines.append(f"{label}: free — part of a job we printed or bound")
        else:
            raw = passes * HANDWORK_RATES[kind]
            line = f"{label}: {passes} pass{'es' if passes != 1 else ''} x Rs.{HANDWORK_RATES[kind]} = Rs.{raw}"
            if total > raw:
                line += f" -> minimum Rs.{HANDWORK_MIN_CHARGE[kind]} applied"
            lines.append(line)

    elif kind == "photo":
        unit = str(meta.get("unit", "set5")).strip().lower()
        qty = max(1, int(meta.get("qty") or 1))
        if unit in PHOTO_RATES:
            rate = PHOTO_RATES[unit]
            total = qty * rate
            what = "set of 5" if unit == "set5" else "full sheet"
            lines.append(f"Photo prints ({what}): {qty} x Rs.{rate} = Rs.{total:.0f}")
        else:
            needs_manual = True
            pending = ", ".join(PHOTO_SIZES_PENDING_RATES)
            lines.append(f"Photo prints ({unit}): NO RATE yet — enter the price "
                         f"(pending: {pending})")

    elif kind == "dtp":
        lang = str(meta.get("language", "english")).strip().lower()
        pages = max(1, int(meta.get("pages") or 1))
        if lang not in DTP_RATES:
            needs_manual = True
            lines.append(f"DTP ({lang}): NO RATE — enter the price")
        else:
            rate = DTP_RATES[lang]
            total = pages * rate
            lines.append(f"DTP {lang.title()}: {pages} pages x Rs.{rate} = Rs.{total:.0f}"
                         " (typing only — printing charged separately)")

    elif kind == "other":
        lines.append(f"{meta.get('description') or 'Other service'}: enter the price")

    # A manual price, once the operator has typed one, is the price. A typed
    # value that is not a number leaves the job flagged AND says so — swallowing
    # it would look identical to not having typed anything, which is how a job
    # goes out unbilled.
    raw_manual = meta.get("manual_price")
    if needs_manual and raw_manual not in (None, ""):
        manual = _as_amount(raw_manual)
        if manual is None:
            lines.append(f"Price {raw_manual!r} is not a number — not applied")
        else:
            total = manual
            needs_manual = False
            lines.append(f"Price entered by staff: Rs.{total:.0f}")

    if meta.get("urgent"):
        total += SERVICE_URGENT_SURCHARGE
        lines.append(f"Urgent: +Rs.{SERVICE_URGENT_SURCHARGE}")

    total = round(total, 2)
    lines.append(f"--- Total: Rs.{total:.2f}")
    return {"total": total, "breakdown": lines, "label": label,
            "needs_manual_price": needs_manual, "unpriced": False}


def _as_amount(value) -> float | None:
    """A money value, or None if it is not one. No exception escapes and none is
    swallowed: the caller decides what to say about a rejected price."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _min_line(what: str, size: str, asked: int, minimum: int, rate: int,
              total: float) -> str:
    """One breakdown line that says out loud when a minimum did the billing."""
    if asked < minimum:
        return (f"{what} {size.upper()}: minimum {minimum} sheets applied "
                f"({asked} brought) — {minimum} x Rs.{rate} = Rs.{total:.0f}")
    return f"{what} {size.upper()}: {asked} x Rs.{rate} = Rs.{total:.0f}"



# ─────────────────────────────────────────────────────────────────────────────
# SECTION 12 — INTER-STORE FINISHING (plan §4.7, B-8)
#
# A job OSP sells and Nattika finishes is an **internal transfer, not a vendor
# job**. The money is one payment from one customer; what changes is how it is
# booked between the two shops.
# ─────────────────────────────────────────────────────────────────────────────

#: What the finishing store keeps, as a fraction of the finishing charge.
#: **Seeded at 100 % on purpose** (plan §4.7): the owner has not set real
#: internal rates, and nothing should block on numbers nobody has decided. At
#: 1.0 the finishing store keeps the whole finishing charge, which is the
#: honest default for two shops with one owner. Per-service overrides go here
#: when there are real numbers to put in them.
FINISHING_INTERNAL_RATE_DEFAULT = 1.0
FINISHING_INTERNAL_RATES: dict[str, float] = {}

#: How the transfer walks. A status may only move forward along this list.
FINISHING_STATUSES = ("sent", "at_finisher", "returned")


def get_internal_rate(finishing: str) -> float:
    """The finishing store's share for this finishing, 0.0-1.0."""
    rate = FINISHING_INTERNAL_RATES.get((finishing or "").strip().lower(),
                                        FINISHING_INTERNAL_RATE_DEFAULT)
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return FINISHING_INTERNAL_RATE_DEFAULT
    return min(1.0, max(0.0, rate))


def split_amounts(print_cost: float, finishing_cost: float,
                  finishing: str = "") -> dict:
    """Book one customer payment across the selling and finishing stores.

    Returns { print_amount, finishing_amount, finishing_internal_amount }.

    * ``print_amount`` — the selling store's, for the printing it did.
    * ``finishing_amount`` — what the customer paid for finishing.
    * ``finishing_internal_amount`` — what the finishing store keeps of that.

    The first two always add up to the quote. The third is a slice of the
    second, never an addition to it: a split that invents money is worse than
    no split at all, and a test pins the arithmetic.
    """
    print_amount = round(max(0.0, _as_amount(print_cost) or 0.0), 2)
    finishing_amount = round(max(0.0, _as_amount(finishing_cost) or 0.0), 2)
    rate = get_internal_rate(finishing)
    return {
        "print_amount": print_amount,
        "finishing_amount": finishing_amount,
        "finishing_internal_amount": round(finishing_amount * rate, 2),
        "internal_rate": rate,
    }


def next_finishing_status(current: str | None) -> str | None:
    """The status that legitimately follows `current`, or None at the end."""
    cur = (current or "").strip().lower()
    if not cur:
        return FINISHING_STATUSES[0]
    if cur not in FINISHING_STATUSES:
        return None
    i = FINISHING_STATUSES.index(cur)
    return FINISHING_STATUSES[i + 1] if i + 1 < len(FINISHING_STATUSES) else None


def is_valid_finishing_move(current: str | None, target: str) -> bool:
    """Only forward, one step at a time, and never off the end.

    A job cannot be marked returned before it was received: the queue at the
    other shop is the only record that the work is physically there, and a
    status that can jump makes that record a guess.
    """
    return next_finishing_status(current) == (target or "").strip().lower()

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 10 — SELF-TEST (run: python rate_card.py)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== rate_card.py self-test ===\n")

    tests = [
        # (desc, pages, sides, layout, expected_sheets)
        ("34p DS 1-up",   34, "ds", "1-up", 17),   # ceil(34/2)=17 sheets
        ("5p  DS 1-up",    5, "ds", "1-up",  3),   # ceil(5/2)=3 sheets
        ("6p  DS 1-up",    6, "ds", "1-up",  3),   # ceil(6/2)=3 sheets
        ("4p  DS 2-up",    4, "ds", "2-up",  1),   # ceil(4/2)=2p -> ceil(2/2)=1 sheet
        ("50p SS 2-up",   50, "ss", "2-up", 25),   # ceil(50/2)=25 sheets
        ("10p SS 1-up",   10, "ss", "1-up", 10),
        ("1p  DS 1-up",    1, "ds", "1-up",  1),   # ceil(1/2)=1 sheet
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
