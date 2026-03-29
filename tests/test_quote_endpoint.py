"""
Tests for print_server.handle_quote() — the /quote endpoint.
Covers the S7-1 fix: colour=col param maps to paper_type=A4_col.

Imports print_server with heavy dependencies stubbed out.
"""

import sys
import os
import types

# ── Stub every external dep print_server tries to import ─────────────────────
_STUBS = [
    "gspread", "google", "google.auth", "google.auth.transport",
    "google.auth.transport.requests", "google.oauth2", "google.oauth2.service_account",
    "websockets", "requests", "pysnmp", "pysnmp.hlapi",
    "watchdog", "watchdog.observers", "watchdog.events",
    "razorpay", "dotenv",
]
for _mod in _STUBS:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

# dotenv.load_dotenv must be callable
sys.modules["dotenv"].load_dotenv = lambda: None  # type: ignore

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import print_server


def quote(params: dict) -> dict:
    """Helper: build qs dict (parse_qs format) and call handle_quote."""
    qs = {k: [str(v)] for k, v in params.items()}
    return print_server.handle_quote(qs)


# ─────────────────────────────────────────────────────────────────────────────
# S7-1: colour=col param
# ─────────────────────────────────────────────────────────────────────────────

class TestColourParam:
    """S7-1: /quote?colour=col should calculate colour pricing, not B&W."""

    def test_colour_col_gives_higher_price_than_bw(self):
        r_col = quote({"pages": 10, "colour": "col", "sides": "ss", "copies": 1})
        r_bw  = quote({"pages": 10, "colour": "bw",  "sides": "ss", "copies": 1})
        assert r_col["total"] > r_bw["total"]

    def test_colour_col_alias(self):
        r1 = quote({"pages": 10, "colour": "col",    "sides": "ss", "copies": 1})
        r2 = quote({"pages": 10, "colour": "colour", "sides": "ss", "copies": 1})
        r3 = quote({"pages": 10, "colour": "color",  "sides": "ss", "copies": 1})
        assert r1["total"] == r2["total"] == r3["total"]

    def test_colour_col_10_sheets_rate_10_per_sheet(self):
        # 10 pages SS col ≤30 → 10 sheets × Rs.10 = Rs.100
        r = quote({"pages": 10, "colour": "col", "sides": "ss", "copies": 1})
        assert r["total"] == 100.0

    def test_colour_bw_3_per_sheet(self):
        # 10 pages SS BW → 10 sheets × Rs.3 = Rs.30
        r = quote({"pages": 10, "colour": "bw", "sides": "ss", "copies": 1})
        assert r["total"] == 30.0

    def test_explicit_paper_type_overrides_colour(self):
        # paper_type=A4_BW should win even if colour=col present
        r = quote({"pages": 10, "paper_type": "A4_BW", "colour": "col",
                   "sides": "ss", "copies": 1})
        assert r["total"] == 30.0

    def test_a3_colour(self):
        r = quote({"pages": 5, "colour": "col", "paper_size": "A3",
                   "sides": "ss", "copies": 1})
        # A3 col SS = Rs.20/sheet, 5 sheets = Rs.100
        assert r["total"] == 100.0

    def test_no_colour_param_defaults_to_bw(self):
        r_default = quote({"pages": 10, "sides": "ss", "copies": 1})
        r_bw      = quote({"pages": 10, "colour": "bw", "sides": "ss", "copies": 1})
        assert r_default["total"] == r_bw["total"]


# ─────────────────────────────────────────────────────────────────────────────
# Basic quote endpoint behaviour
# ─────────────────────────────────────────────────────────────────────────────

class TestQuoteEndpoint:
    def test_returns_dict(self):
        r = quote({"pages": 10, "sides": "ss", "copies": 1})
        assert isinstance(r, dict)

    def test_has_total(self):
        r = quote({"pages": 10, "sides": "ss", "copies": 1})
        assert "total" in r

    def test_has_breakdown(self):
        r = quote({"pages": 10, "sides": "ss", "copies": 1})
        assert "breakdown" in r

    def test_finishing_spiral(self):
        r_plain  = quote({"pages": 34, "sides": "ds", "copies": 1})
        r_spiral = quote({"pages": 34, "sides": "ds", "copies": 1, "finishing": "spiral"})
        assert r_spiral["total"] > r_plain["total"]

    def test_finishing_thermal(self):
        """S7-5: /quote?finishing=thermal should return non-zero finishing cost."""
        r = quote({"pages": 20, "sides": "ss", "copies": 1, "finishing": "thermal"})
        assert r["finishing_cost"] > 0

    def test_urgent_flag(self):
        r_normal = quote({"pages": 20, "sides": "ss", "copies": 1,
                          "finishing": "soft"})
        r_urgent = quote({"pages": 20, "sides": "ss", "copies": 1,
                          "finishing": "soft", "urgent": "true"})
        assert r_urgent["total"] > r_normal["total"]

    def test_student_flag(self):
        r_normal  = quote({"pages": 50, "sides": "ss", "copies": 1})
        r_student = quote({"pages": 50, "sides": "ss", "copies": 1,
                           "is_student": "true"})
        assert r_student["total"] < r_normal["total"]

    def test_copies_scale_total(self):
        r1 = quote({"pages": 10, "sides": "ss", "copies": "1"})
        r3 = quote({"pages": 10, "sides": "ss", "copies": "3"})
        # finishing is 0, so total scales linearly
        assert r3["total"] == r1["total"] * 3

    def test_ds_fewer_sheets_than_ss(self):
        r_ss = quote({"pages": 34, "sides": "ss", "copies": 1})
        r_ds = quote({"pages": 34, "sides": "ds", "copies": 1})
        # DS has fewer sheets (18 vs 34) → cheaper for BW (same per-sheet rate)
        assert r_ds["sheets"] < r_ss["sheets"]
        assert r_ds["total"] < r_ss["total"]
