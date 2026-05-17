"""Claude Vision per-page analysis.

Sends (page PNG + extracted text hint) to Claude Sonnet and returns a
structured JSON description of the page: elements in document order,
each with type (heading / body / list_item / table / image / caption),
text content, alignment, bold/italic spans, and (for tables) cell matrix.

The PDF text layer is included as a hint so Claude doesn't hallucinate
OCR — it can copy text verbatim from the hint and use the image for
visual structure (alignment, bold spans, image positions, table grid).
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

from anthropic import Anthropic


VISION_MODEL = "claude-sonnet-4-5"  # latest Sonnet — best vision quality
MAX_TOKENS = 4096

# Structured JSON schema we ask Claude to return per page.
SYSTEM_PROMPT = """You are a document structure analyzer for a print-shop
formatter. You receive ONE page image from a student academic project
(college / university report) plus the raw text extracted from that page.

Return ONLY a JSON object (no markdown fences, no commentary) matching
this schema:

{
  "page_kind": "title" | "certificate" | "acknowledgement" | "declaration"
              | "abstract" | "toc" | "list_of_figures" | "list_of_tables"
              | "chapter" | "references" | "appendix" | "blank" | "other",
  "elements": [
    {
      "type": "heading" | "body" | "list_item" | "table" | "image"
            | "caption" | "page_number" | "footer" | "skip",
      "level": 1 | 2 | 3 | null,                // for headings only
      "text": "verbatim text (copy from hint when possible)",
      "alignment": "left" | "center" | "right" | "justify",
      "bold": true | false,
      "italic": true | false,
      "list_style": "bullet" | "number" | null, // for list_item only
      "table_rows": [["cell", "cell"], ...] | null,  // for table only
      "image_caption": "string" | null,         // for image only
      "image_position": "inline" | "centered" | "full_width" | null
    }
  ],
  "notes": "optional one-line note about anything unusual"
}

RULES:
- Walk the page top-to-bottom, left-to-right. Output elements in document order.
- For headings: level 1 = chapter title (e.g. "CHAPTER 1 INTRODUCTION"),
  level 2 = section, level 3 = sub-section.
- Title page elements (college name, project title, student names,
  guide/HOD blocks) -> use "heading" with appropriate level + alignment.
  Year/date on title page is usually centered.
- For tables: extract as 2D array. First row is the header. Preserve
  empty cells as "".
- For figures: emit an "image" element AT THE POSITION it appears on the
  page. If a caption follows ("Fig 3.2 ..."), emit a separate "caption"
  element right after.
- Page numbers, headers, footers -> type "page_number" or "footer" (will
  be auto-managed by Word, not rendered inline).
- Decorative elements / borders / horizontal rules -> type "skip".
- Do NOT invent text. If unsure, copy from the text hint verbatim.
- Output VALID JSON. No trailing commas. No comments inside.
"""


def _client() -> Anthropic:
    """Build an Anthropic client, loading .env if the env var is empty."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        try:
            from dotenv import dotenv_values
            vals = dotenv_values(".env")
            key = vals.get("ANTHROPIC_API_KEY", "") or ""
        except Exception:
            pass
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY missing. Set in env or .env"
        )
    return Anthropic(api_key=key)


def analyze_page(
    page_png: bytes,
    text_hint: str,
    page_no: int,
    total_pages: int,
) -> dict[str, Any]:
    """Send one page to Claude Vision, return parsed JSON dict.

    Args:
        page_png: PNG bytes of the rendered page (recommend 150 DPI).
        text_hint: Raw text from PDF text layer (newline-separated).
        page_no: 1-indexed page number (for the prompt context only).
        total_pages: Total pages in the document.

    Returns:
        Dict matching the schema in SYSTEM_PROMPT. On parse failure,
        returns {"page_kind": "other", "elements": [], "notes":
        "parse_failure: <error>", "_raw": "<response text>"}.
    """
    client = _client()
    b64 = base64.standard_b64encode(page_png).decode("ascii")

    user_text = (
        f"Page {page_no} of {total_pages}.\n\n"
        f"--- TEXT EXTRACTED FROM THIS PAGE (raw, may have layout noise) ---\n"
        f"{text_hint[:8000]}\n"
        f"--- END TEXT HINT ---\n\n"
        f"Return the JSON object now."
    )

    resp = client.messages.create(
        model=VISION_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64,
                    },
                },
                {"type": "text", "text": user_text},
            ],
        }],
    )

    raw = "".join(
        block.text for block in resp.content if hasattr(block, "text")
    ).strip()

    # Strip ```json fences if Claude adds them despite instructions.
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)

    try:
        parsed = json.loads(raw)
        parsed["_usage"] = {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        }
        return parsed
    except json.JSONDecodeError as e:
        return {
            "page_kind": "other",
            "elements": [],
            "notes": f"parse_failure: {e}",
            "_raw": raw[:2000],
            "_usage": {
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            },
        }
