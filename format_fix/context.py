"""Shared context object passed to every section handler.

Pre-computes everything a handler might need (body font size baseline,
decorative-image xrefs, survey-table parses) so handlers stay stateless and
simple. Built once per pipeline run.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz

from . import extraction


_CONFIG_DIR = Path(__file__).parent / "configs"


def load_university_config(university_id: str) -> dict:
    """Load a university JSON config. Falls back to ktu.json on missing."""
    path = _CONFIG_DIR / f"{university_id}.json"
    if not path.exists():
        path = _CONFIG_DIR / "ktu.json"
    return json.loads(path.read_text(encoding="utf-8"))


# Survey-table regex (ported from printosky/docx_engine.py)
SURVEY_TABLE_RE = re.compile(
    r"Table\s+(\d+)\s*:\s*(.+?)(?=\n)"
    r".*?TRUE"
    r"(?:\n+\s*(\d+)\s+(\d+)|\n+\s*(\d+)\s*\n+\s*(\d+))"
    r".*?FALSE"
    r"(?:\n+\s*(\d+)\s+(\d+)|\n+\s*(\d+)\s*\n+\s*(\d+))"
    r".*?TOTAL"
    r"(?:\n+\s*(\d+)\s+(\d+)|\n+\s*(\d+)\s*\n+\s*(\d+))",
    re.DOTALL | re.IGNORECASE,
)

# Likert 5-point variant: "Strongly agree / Agree / Neutral / Disagree /
# Strongly disagree" -- commerce/management surveys (e.g. Anju Google-Pay).
LIKERT_CAPTION_RE = re.compile(
    r"^\s*Table\s*(\d+(?:\.\d+)?)\s*[:\-]?\s*(.*)$",
    re.IGNORECASE,
)
LIKERT_OPTIONS = [
    ("Strongly agree",    ["strongly agree"]),
    ("Agree",             ["agree"]),
    ("Neutral",           ["neutral"]),
    ("Disagree",          ["disagree"]),
    ("Strongly disagree", ["strongly disagree"]),
]
_NUM_RE   = re.compile(r"^\d+$")
_PCT_RE   = re.compile(r"^\d+(?:\.\d+)?\s*%?$")
_TOTAL_RE = re.compile(r"^total$", re.IGNORECASE)


def parse_survey_tables(text_block: str) -> list[dict]:
    """Pull TRUE/FALSE survey tables out of a page-level text dump."""
    out: list[dict] = []
    for m in SURVEY_TABLE_RE.finditer(text_block):
        g = m.groups()

        def first_pair(a, b, c, d):
            if a is not None and b is not None:
                return int(a), int(b)
            return int(c or 0), int(d or 0)

        true_n,  true_pct  = first_pair(*g[2:6])
        false_n, false_pct = first_pair(*g[6:10])
        total_n, total_pct = first_pair(*g[10:14])
        out.append({
            "kind":      "yesno",
            "n":         int(g[0]),
            "statement": g[1].strip(),
            "true_n":    true_n,
            "true_pct":  true_pct,
            "false_n":   false_n,
            "false_pct": false_pct,
            "total_n":   total_n,
            "total_pct": total_pct,
        })
    return out


_HEADER_HINTS = ("option", "respondents", "respondent", "percentage",
                  "no. of", "frequency", "no.of")
# A short label is something like "Very Satisfied", "Strongly disagree",
# "Highly influenced" — 1-4 words, mostly letters, no digits.
_LABEL_WORDS_RE = re.compile(r"^[A-Za-z][A-Za-z \-/]{1,40}$")


def _looks_like_label(s: str) -> bool:
    if not _LABEL_WORDS_RE.match(s):
        return False
    if _TOTAL_RE.match(s):
        return False
    if s.lower() in _HEADER_HINTS:
        return False
    words = s.split()
    return 1 <= len(words) <= 4


def parse_likert_tables(lines: list[str]) -> list[dict]:
    """Extract N-point survey-style tables from a page's text lines.

    PyMuPDF emits the columns sequentially: caption -> statement -> column
    headers -> row label -> count -> percent -> next row label -> ... ->
    Total -> count -> percent.

    We scan for the caption, walk past header hints, then collect rows of
    shape (text-label, integer-count, percent). This is label-agnostic so
    it works for "Strongly agree/Disagree", "Very Satisfied/Dissatisfied",
    "Very Attractive/Unattractive", "Highly influenced", etc.
    """
    out: list[dict] = []
    n_lines = len(lines)
    i = 0
    while i < n_lines:
        raw = lines[i].strip()
        cap = LIKERT_CAPTION_RE.match(raw)
        if not cap:
            i += 1
            continue
        table_id = cap.group(1)
        statement = cap.group(2).strip()
        j = i + 1
        if not statement and j < n_lines:
            cand = lines[j].strip()
            if (cand and not LIKERT_CAPTION_RE.match(cand)
                    and not _NUM_RE.match(cand)):
                statement = cand
                j += 1

        window_end = min(j + 80, n_lines)
        rows: list[dict] = []
        total_n = 0
        total_pct: float = 100.0
        k = j
        last_k = k
        # Skip over header hint lines
        while k < window_end:
            ln = lines[k].strip().lower()
            if any(h in ln for h in _HEADER_HINTS):
                k += 1
                continue
            break

        while k < window_end:
            ln = lines[k].strip()
            if not ln:
                k += 1
                continue
            if _TOTAL_RE.match(ln):
                # Grab total count + total percent from following lines
                look = k + 1
                cap_stop = min(k + 6, window_end)
                while look < cap_stop:
                    cand = lines[look].strip()
                    if cand:
                        if _NUM_RE.match(cand) and total_n == 0:
                            total_n = int(cand)
                        elif _PCT_RE.match(cand):
                            total_pct = float(cand.rstrip("%").strip())
                            look += 1
                            break
                    look += 1
                last_k = look
                break
            if LIKERT_CAPTION_RE.match(ln):
                break  # next table starts here
            if _looks_like_label(ln):
                # Look ahead for count + pct in next 1-4 non-empty lines
                count_val: int | None = None
                pct_val: float | None = None
                look = k + 1
                stop_at = min(k + 5, window_end)
                # Allow multi-line labels ("Strongly\ndisagree")
                while look < stop_at:
                    cand = lines[look].strip()
                    if not cand:
                        look += 1
                        continue
                    if _NUM_RE.match(cand) and count_val is None:
                        count_val = int(cand)
                    elif _PCT_RE.match(cand) and pct_val is not None is False and count_val is not None:
                        pct_val = float(cand.rstrip("%").strip())
                        look += 1
                        break
                    elif _PCT_RE.match(cand) and count_val is not None:
                        pct_val = float(cand.rstrip("%").strip())
                        look += 1
                        break
                    elif _looks_like_label(cand) and count_val is None:
                        # Two-line label like "Strongly\ndisagree"
                        ln = (ln + " " + cand).strip()
                    elif _TOTAL_RE.match(cand):
                        break
                    look += 1
                if count_val is not None and pct_val is not None:
                    rows.append({"label": ln,
                                  "count": count_val,
                                  "pct":   pct_val})
                    k = look
                    last_k = k
                    continue
            k += 1

        if len(rows) >= 3:
            out.append({
                "kind":      "likert",
                "id":        table_id,
                "n":         table_id,
                "statement": statement or f"Table {table_id}",
                "rows":      rows,
                "total_n":   total_n or sum(r["count"] for r in rows),
                "total_pct": total_pct,
            })
            i = max(last_k, j + 1)
        else:
            i = j
    return out


@dataclass
class Context:
    """Shared, immutable per-pipeline context handed to every handler."""

    pdf:           fitz.Document
    config:        dict
    university_id: str
    skip_set:      set[int] = field(default_factory=set)

    # Pre-computed per-doc state
    body_pt:           float          = 12.0
    decorative_xrefs:  set[int]       = field(default_factory=set)
    survey_pages:      dict[int, list[dict]] = field(default_factory=dict)

    # Mutable cross-page state used by certain handlers
    in_form_section: bool = False
    in_toc_section:  bool = False

    # Convenience — derived once for performance
    front_matter_page_limit: int = 4

    @classmethod
    def build(cls,
              pdf: fitz.Document,
              university_id: str,
              skip_pages: list[int] | None = None,
              front_matter_page_limit: int = 4) -> "Context":
        """Construct a fully-prepared Context. One scan per shared resource."""
        config = load_university_config(university_id)
        skip_set = {p - 1 for p in (skip_pages or [])}
        body_pt = extraction.estimate_body_size(pdf)
        decorative_xrefs = extraction.build_decorative_xrefs(pdf)

        # First-pass: identify survey-table pages so dispatcher can route.
        # Try yes/no shape first; fall back to Likert 5-point shape (which
        # is the dominant form in commerce / management surveys).
        survey_pages: dict[int, list[dict]] = {}
        for page_no in range(len(pdf)):
            page = pdf[page_no]
            # Use raw page text (which preserves line breaks PyMuPDF sees)
            # for both parsers — block-merging strips the column structure
            # the Likert parser relies on.
            page_text  = page.get_text()
            page_lines = [ln for ln in page_text.splitlines()
                          if ln.strip()]
            yesno  = parse_survey_tables(page_text)
            likert = parse_likert_tables(page_lines) if not yesno else []
            tables = yesno + likert
            if tables:
                survey_pages[page_no] = tables

        return cls(
            pdf=pdf,
            config=config,
            university_id=university_id,
            skip_set=skip_set,
            body_pt=body_pt,
            decorative_xrefs=decorative_xrefs,
            survey_pages=survey_pages,
            front_matter_page_limit=front_matter_page_limit,
        )

    @property
    def body_size_pt(self) -> int:
        return int(self.config.get("body_size_pt", 12))

    @property
    def heading_size_pt(self) -> int:
        h1 = self.config.get("heading1") or {}
        return int(h1.get("size_pt", 14))

    @property
    def body_font(self) -> str:
        return self.config.get("body_font", "Times New Roman")
