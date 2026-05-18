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
      "level": 1 | 2 | 3 | null,
      "runs": [
        {"text": "verbatim text segment", "bold": false, "italic": false}
      ],
      "alignment": "left" | "center" | "right" | "justify",
      "list_style": "bullet" | "number" | null,
      "table_rows": [
        [
          {"runs": [{"text": "cell text", "bold": false, "italic": false}],
           "alignment": "left"}
        ]
      ] | null,
      "image_caption": "string" | null,
      "image_position": "inline" | "centered" | "full_width" | null
    }
  ],
  "notes": "optional one-line note about anything unusual"
}

CRITICAL RULES:

1. Bold and italic detection (CRITICAL - get this right):

   STEP A - look at every paragraph and identify bold/italic visually.
   Bold text has THICKER strokes than the surrounding text. Italic text
   has SLANTED letters. Don't be conservative - if something looks bold
   or italic on the page, mark it.

   STEP B - decide if the paragraph is uniformly bold or mixed:

   (a) ENTIRE paragraph is bold (e.g. a project title, section heading,
       sub-heading, or a bold-only line):
       "runs": [{"text": "WATER TANK CLEANER", "bold": true}]

   (b) ENTIRE paragraph is italic (e.g. a designation line like
       "Lecturer in Mechanical Engineering"):
       "runs": [{"text": "Lecturer in Mechanical Engineering", "italic": true}]

   (c) MIXED bold + regular (e.g. "Definition: Water tank cleaners are
       mechanical devices..."), emit MULTIPLE runs:
       "runs": [
         {"text": "Definition: ", "bold": true},
         {"text": "Water tank cleaners are mechanical devices..."}
       ]

   (d) Plain (no bold or italic):
       "runs": [{"text": "the regular text here"}]

   IMPORTANT RULES:
   - Title pages have LOTS of bold + italic. Project title is usually
     bold. Student names are sometimes bold. Year is often bold.
     Designations ("Lecturer in X", "Head of Department") are often
     italic. Mark all of these.
   - DO NOT split a bold-prefixed paragraph into a list_item just
     because the bold prefix exists. Keep as body with mixed runs.
   - Headings (type="heading") ALSO need bold:true on their runs - even
     though Word's heading style is bold by default, mark it explicitly
     so we don't lose the signal if the style fails.

   ADDITIONAL: multi-line project titles
   - A long title that wraps to 2-3 lines (e.g. "INCLUSIVE EDUCATION
     FOR CHILDREN / WITH SPECIAL NEEDS: / A COMMUNITY AWARENESS
     APPROACH") should be emitted as ONE heading element with the lines
     joined into a single text run, not three separate H1 elements.
     Use a space between joined lines.

2. Element ordering: top-to-bottom, left-to-right. Multi-column pages
   are read column by column (left column fully, then right column).

3. Headings:
   - level 1 = chapter title ("CHAPTER 1 INTRODUCTION", "1. INTRODUCTION")
   - level 2 = section ("1.1 Overview")
   - level 3 = sub-section ("1.1.2 Sub-topic")

4. Title page elements (college name, project title, names, guide/HOD):
   - Use "heading" with appropriate level + alignment.
   - Year/date is usually centered.
   - When a guide name (heading level 3) is followed by their designation
     (italic body line) AND department / college lines, emit each as
     separate elements. The renderer will tighten the layout.

5. Tables:
   - Extract as 2D array of CELL objects (not bare strings).
   - Each cell has runs[] for mixed formatting, and alignment.
   - First row is the header.
   - Preserve empty cells as {"runs": [{"text": ""}], "alignment": "left"}.

6. Figures:
   - Emit an "image" element AT THE POSITION it appears.
   - If a caption follows ("Fig 3.2 ..."), emit a separate "caption"
     element right after.

7. Numbered lists:
   - For list_item with list_style="number", DO NOT include the leading
     number in runs[]. Just the actual content.
     WRONG: "runs": [{"text": "1. Water is essential."}]
     RIGHT: "runs": [{"text": "Water is essential."}]
   - Same for bullet lists - don't include the bullet character.

8. Skip these:
   - Page numbers, headers, footers -> type "page_number" or "footer"
     (Word will auto-manage these).
   - Decorative borders, rules, watermarks -> type "skip".

9. Output rules:
   - VALID JSON. No trailing commas. No markdown fences. No comments.
   - Do NOT invent text. Copy verbatim from text hint when possible.
   - Every run MUST have a "text" field. "bold" and "italic" default
     to false if omitted.
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
        Dict matching the schema in SYSTEM_PROMPT, plus _usage:
          {input_tokens, output_tokens, cache_read_tokens,
           cache_write_tokens, model, elapsed_ms, error?}
        On parse failure, returns a minimal dict with page_kind="other"
        and the raw response text in _raw for debugging.
    """
    import time as _time
    t0 = _time.time()
    client = _client()
    b64 = base64.standard_b64encode(page_png).decode("ascii")

    user_text = (
        f"Page {page_no} of {total_pages}.\n\n"
        f"--- TEXT EXTRACTED FROM THIS PAGE (raw, may have layout noise) ---\n"
        f"{text_hint[:8000]}\n"
        f"--- END TEXT HINT ---\n\n"
        f"Return the JSON object now."
    )

    # B4: prompt caching on SYSTEM_PROMPT. The system block is identical
    # for every page call within a document (and across documents within
    # the 5-min TTL), so we mark it cache_control: ephemeral. First call
    # of a session writes the cache (+25% cost on those tokens); every
    # subsequent call within 5 min reads from cache (-90% cost).
    resp = client.messages.create(
        model=VISION_MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
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

    # Cache token usage (None on older SDKs / when caching disabled)
    cache_read = getattr(resp.usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(resp.usage, "cache_creation_input_tokens", 0) or 0

    usage = {
        "input_tokens":       resp.usage.input_tokens,
        "output_tokens":      resp.usage.output_tokens,
        "cache_read_tokens":  cache_read,
        "cache_write_tokens": cache_write,
        "model":              VISION_MODEL,
        "elapsed_ms":         int((_time.time() - t0) * 1000),
    }

    try:
        parsed = json.loads(raw)
        parsed["_usage"] = usage
        return parsed
    except json.JSONDecodeError as e:
        usage["error"] = f"parse_failure: {e}"
        return {
            "page_kind": "other",
            "elements": [],
            "notes": f"parse_failure: {e}",
            "_raw": raw[:2000],
            "_usage": usage,
        }
