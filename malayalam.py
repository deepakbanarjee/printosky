"""
malayalam.py — Malayalam text handling for the transcription pipeline.

Two steps that every transcript render needs, in this order:

    processed = replace_chillus(line)
    segments  = split_malayalam_english(processed)

`split_malayalam_english` exists because a mixed line has to be laid out with a
Malayalam font on the Malayalam runs and a Latin font on the rest; it returns
[(text, is_malayalam), ...] covering the whole input in order.

This was three identical copies — api/index.py (the /api/transcripts/export-docx
endpoint), tools/cloud_transcription_worker.py and tools/pdf_tools_server.py —
each with its own chillu table.


KNOWN DEFECT IN CHILLU_MAP — see the table below
------------------------------------------------
The map's keys are shifted one codepoint against the values they should carry,
so five of the six chillu letters decompose to the wrong consonant. Real output
today:

    അവൻ  ->  അവണ്‍     (should be അവന്‍)   "avan"
    അവൾ  ->  അവന്‍     (should be അവള്‍)   "aval"
    വർഷം ->  വള്‍ഷം    (should be വര്‍ഷം)  "varsham"
    കാൽ  ->  കാര്‍     (should be കാല്‍)   "kaal"

and ൿ (CHILLU K) has no correct entry at all — it currently picks up ൽ's value.
Only ൺ (CHILLU NN) is right.

The trailing comments are the tell: each one names the letter the *value*
decomposes to, which is what the author intended the key to be. The keys were
then written one codepoint off.

This is preserved exactly as-is here rather than fixed, because fixing it
changes the text in transcripts customers have already received and is a call
for someone who reads Malayalam. CORRECTED_CHILLU_MAP below is the intended
table, ready to swap in once that call is made — swapping the name in
`replace_chillus` is the whole change.
"""

import re

# The table as it has always shipped. Keys are one codepoint off their values;
# see the module docstring. Do not "tidy" this without reading that first.
CHILLU_MAP = {
    "ൻ": "ണ്‍",  # ൺ
    "ർ": "ള്‍",  # ൾ
    "ൽ": "ര്‍",  # ർ
    "ൾ": "ന്‍",  # ൻ
    "ൿ": "ല്‍",  # ൽ
    "ൺ": "ണ്‍",  # ൺ
}

# What the table should be: each atomic chillu to its base consonant + virama +
# ZWJ, the standard Unicode equivalence. Not yet in use — see the docstring.
CORRECTED_CHILLU_MAP = {
    "ൺ": "ണ്‍",  # ൺ CHILLU NN -> ണ്‍
    "ൻ": "ന്‍",  # ൻ CHILLU N  -> ന്‍
    "ർ": "ര്‍",  # ർ CHILLU RR -> ര്‍
    "ൽ": "ല്‍",  # ൽ CHILLU L  -> ല്‍
    "ൾ": "ള്‍",  # ൾ CHILLU LL -> ള്‍
    "ൿ": "ക്‍",  # ൿ CHILLU K  -> ക്‍
}

# Malayalam block, plus ZWJ — which carries the chillu forms the step above
# produces, so it has to count as Malayalam when the runs are split.
_MALAYALAM_RUN = re.compile(r"([ഀ-ൿ‍]+)")


def _is_malayalam(part: str) -> bool:
    return any(("ഀ" <= ch <= "ൿ") or (ch == "‍") for ch in part)


def replace_chillus(text: str) -> str:
    """Rewrite atomic chillu letters as base consonant + virama + ZWJ."""
    for chillu, replacement in CHILLU_MAP.items():
        text = text.replace(chillu, replacement)
    return text


def split_malayalam_english(text: str) -> list[tuple[str, bool]]:
    """Split into [(run, is_malayalam), ...], in order, covering all of `text`."""
    segments = []
    for part in _MALAYALAM_RUN.split(text):
        if not part:
            continue
        segments.append((part, _is_malayalam(part)))
    return segments
