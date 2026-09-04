#!/usr/bin/env python3
"""
Code Google-Maps review dumps against the shared codebook (CODEBOOK.md) so that
print shops in Thrissur and Ernakulam can be compared on the same axes.

    python3 code_reviews.py corpus/thrissur-kundham-college-road.txt
    python3 code_reviews.py --compare corpus/*.txt
    python3 code_reviews.py --json corpus/*.txt > coded.json

Input is the text you get by selecting the reviews pane on a Google Maps listing
and copying it. The parser expects that shape:

    <reviewer name>
    Local Guide·29 reviews·27 photos
    5 months ago
    <review text>
    <Shop name> (owner)
    5 months ago
    <owner reply>

Nothing here talks to Google. Collect the dumps by hand (see README.md) — this
only reads what is already in corpus/.
"""

import argparse
import json
import pathlib
import re
import sys
from collections import Counter

# --- parsing -----------------------------------------------------------------

CRED_RE = re.compile(r"^(?:Local Guide·)?[\d,]+\s+reviews?(?:·[\d,]+\s+photos?)?$")
DATE_RE = re.compile(
    r"^(?:Edited\s+)?(a|an|\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago$",
    re.I,
)
OWNER_RE = re.compile(r"\(owner\)\s*$")
NOISE_RE = re.compile(r"^(Sort by|Most relevant|Newest|Highest|Lowest|Photo \d+ in review by)")

MONTHS = {"second": 0, "minute": 0, "hour": 0, "day": 1 / 30, "week": 0.25,
          "month": 1, "year": 12}


def age_months(date_line):
    """'5 months ago' -> 5.0. Returns None when the line isn't a date."""
    m = DATE_RE.match(date_line)
    if not m:
        return None
    qty = 1 if m.group(1).lower() in ("a", "an") else int(m.group(1))
    return round(qty * MONTHS[m.group(2).lower()], 2)


def has_letters(line):
    return any(ch.isalpha() for ch in line)


def parse(raw):
    """Split a pasted reviews pane into review records."""
    lines = [ln.strip() for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln and not NOISE_RE.match(ln)]

    # A credentials line marks a review; the line above it is the reviewer name.
    starts = [i for i, ln in enumerate(lines) if CRED_RE.match(ln) and i > 0]
    owners = {i for i, ln in enumerate(lines) if OWNER_RE.search(ln)}

    records = []
    for n, i in enumerate(starts):
        end = starts[n + 1] - 1 if n + 1 < len(starts) else len(lines)
        block = lines[i + 1:end]

        reply_at = next((j for j, ln in enumerate(block)
                         if OWNER_RE.search(ln) or (i + 1 + j) in owners), None)
        body, reply = (block[:reply_at], block[reply_at:]) if reply_at is not None else (block, [])

        posted = age_months(body[0]) if body else None
        if posted is not None:
            body = body[1:]

        text = " ".join(ln for ln in body if has_letters(ln))
        reply_text = " ".join(ln for ln in reply[1:] if has_letters(ln))
        # drop the owner's own date line from the reply
        reply_text = DATE_RE.sub("", reply_text).strip()

        records.append({
            "reviewer": lines[i - 1],
            "credentials": lines[i],
            "age_months": posted,
            "text": text,
            "owner_reply": reply_text,
            "truncated": "…More" in text or text.endswith("More"),
        })
    return records


# --- coding ------------------------------------------------------------------
# Patterns are matched case-insensitively against the review text. Extend these
# rather than hand-tagging; then re-run every corpus so columns stay comparable.

LEXICON = {
    "P_PRICE": r"cheap|afford|low (?:price|rate|cost)|reasonab|budget|economic|less cost|lowest|minimum amount|worth it|pocket friendly|cost effective",
    "P_SPEED": r"\bfast\b|quick|prompt|speed|in (?:no|less) time|within minutes|no waiting|didn'?t have to wait|on time|immediate|timely",
    "P_BULK": r"\bbulk\b|large (?:quantity|number|amount)|thousand page|in bulk|large document",
    "P_STAFF": r"friendly|helpful|polite|patient|supportive|good (?:staff|behaviou?r|service|dealing|customer)|nice (?:to|people|staff|behaviou?r)|courteous|warmth|customer friendly",
    "P_QUALITY": r"good (?:quality|print)|clear cop|quality (?:print|is good)|neat|clarity|better quality|print quality (?:is|was) good",
    "P_REMOTE": r"whats?a+pp|e-?mail|pen ?drive|online (?:order|payment|application)|upi|send(?:ing)? (?:the )?(?:docs|data|files)|electronic means",
    "P_RANGE": r"bind|spiral|laminat|\bdtp\b|project|seminar|thesis|scan|plotter|record|book print|stud(?:y|ies) material|question bank",
    "P_STUDENT": r"student|college|school|blessing for",
    "N_STAFF": r"rude|bad (?:behaviou?r|attitude|dealing)|worst (?:behaviou?r|attitude)|arrogan|unprofessional|ignoran|attitude is (?:very )?bad|beg them|not (?:even )?interested to attend|government office like|treating customers|poor customer|bad customer|terrible customer|customer service is too bad|need to beg|not keen",
    "N_WAIT": r"wait|crowd|conjust|congest|rush|long time|hours?\b|delay|busy place|too many|understaff|queue|lagg|takes? time|have patience",
    "N_STATUS": r"follow ?up|ask every ?time|every ?time otherwise|reminder|no (?:update|response|reply)|make (?:you|as) wait|already printed",
    "N_BILLING": r"overpric|over ?charg|charged? (?:for|extra|rs|₹)|expensive|costlier|hidden|extra (?:charge|money)|make (?:as|us) paid|they charge",
    "N_QUALITY": r"(?:poor|bad|worst|not) (?:paper|print|quality)|not clear|unclear|unwanted lines|blur|quality (?:was|is) poor|shoddy|print quality wasn'?t",
    "N_ERROR": r"duplicate(?:s| of)|unwanted pages|wrong (?:file|print|page)|missing page|mistake|reprint|not (?:the )?correct",
    "N_SKILL": r"untrained|unskilled|incompetent|don'?t know how|can'?t (?:do|so) basic|new employee|change in employees|catch ?up to the speed",
    "N_LOCATION": r"duplicate shops?|right (?:place|shop)|wrong (?:place|shop)|which (?:branch|shop)|same name|similar names|hard to find",
    "N_CONTACT": r"phone number|no phone|contact number|update phone|no online business|no (?:phone )?inquiry|further enquiry",
    "N_UPTIME": r"electricity|power ?cut|network|server (?:down|issue)|machine (?:not|is not) work|closed",
    # Manglish / Malayalam
    "P_PRICE_ML": r"kuranja|vila kuravu",
    "P_QUALITY_ML": r"pwoli|kollam|nannay|super",
    "N_QUALITY_ML": r"mosham|moodesh|waste|wastw",
}

# Codes that are language variants of a base code, folded in before reporting.
FOLD = {"P_PRICE_ML": "P_PRICE", "P_QUALITY_ML": "P_QUALITY", "N_QUALITY_ML": "N_QUALITY"}

NEG_MARKERS = r"worst|bad|poor|rude|not recommend|disappoint|unsatisf|terrible|horrible|never|avoid|frustrat|sorry to|waste|wastw|mosham|moodesh|overpric|cheat"
POS_MARKERS = r"\bbest\b|excellent|great|good|nice|super|recommend|love|thank|awesome|satisf|helpful|pwoli|kollam"

M_DEFLECT = r"duplicate shops?|right place|came the right|visit the right|not our|not responsible"
M_BOILERPLATE = (r"thank you for the feedback|thank you so much for your kind words|we really appreciate you taking the time|sorry for the inconvenience")
M_APOLOGY = (r"genuinely sorry|fell short|did not meet your expectation|"
             r"we (?:are|were) wrong|apolog|will (?:be )?(?:fixed|corrected|addressed)")


def code_one(rec):
    text = rec["text"].lower()
    codes = set()
    if len(rec["text"].split()) <= 3:
        codes.add("LOW_CONTENT")
    else:
        for code, pattern in LEXICON.items():
            if re.search(pattern, text):
                codes.add(FOLD.get(code, code))
    if rec["truncated"]:
        codes.add("TRUNCATED")

    neg = len(re.findall(NEG_MARKERS, text))
    pos = len(re.findall(POS_MARKERS, text))
    polarity = "neg" if neg > pos else "pos" if pos > neg else "mixed"

    reply = rec["owner_reply"].lower()
    if reply:
        codes.add("M_REPLIED")
        if re.search(M_DEFLECT, reply):
            codes.add("M_DEFLECT")
        if re.search(M_BOILERPLATE, reply):
            codes.add("M_BOILERPLATE")
        if re.search(M_APOLOGY, reply):
            codes.add("M_APOLOGY")
        complained = any(c.startswith("N_") for c in codes)
        praised = any(c.startswith("P_") for c in codes)
        if re.search(r"kind words", reply) and (polarity == "neg" or (complained and not praised)):
            codes.add("M_MISMATCH")

    rec["codes"] = sorted(codes)
    rec["polarity"] = polarity
    return rec


ORDER = ["P_PRICE", "P_SPEED", "P_BULK", "P_STAFF", "P_QUALITY", "P_REMOTE",
         "P_RANGE", "P_STUDENT", "N_STAFF", "N_WAIT", "N_STATUS", "N_BILLING",
         "N_QUALITY", "N_ERROR", "N_SKILL", "N_LOCATION", "N_CONTACT",
         "N_UPTIME", "M_REPLIED", "M_DEFLECT", "M_BOILERPLATE", "M_MISMATCH",
         "M_APOLOGY", "LOW_CONTENT", "TRUNCATED"]


def summarise(path):
    recs = [code_one(r) for r in parse(pathlib.Path(path).read_text(encoding="utf-8"))]
    counts = Counter(c for r in recs for c in r["codes"])
    substantive = [r for r in recs if "LOW_CONTENT" not in r["codes"]]
    recent = [r for r in substantive if (r["age_months"] or 999) <= 12]
    return {
        "shop": pathlib.Path(path).stem,
        "n": len(recs),
        "n_substantive": len(substantive),
        "n_last_12mo": len(recent),
        "polarity": dict(Counter(r["polarity"] for r in substantive)),
        "neg_share_last_12mo": round(
            sum(1 for r in recent if r["polarity"] == "neg") / len(recent), 3
        ) if recent else None,
        "counts": {c: counts.get(c, 0) for c in ORDER},
        "pct_of_substantive": {
            c: round(100 * counts.get(c, 0) / len(substantive), 1) for c in ORDER
        } if substantive else {},
        "records": recs,
    }


def report(s):
    out = [f"# {s['shop']}", "",
           f"- reviews parsed: **{s['n']}** ({s['n_substantive']} substantive, "
           f"{s['n'] - s['n_substantive']} one-word)",
           f"- last 12 months: {s['n_last_12mo']}"
           + (f" — negative share **{s['neg_share_last_12mo']:.0%}**"
              if s["neg_share_last_12mo"] is not None else ""),
           f"- polarity (substantive): {s['polarity']}", "",
           "| code | n | % of substantive |", "|---|---:|---:|"]
    for c in ORDER:
        if s["counts"][c]:
            out.append(f"| {c} | {s['counts'][c]} | {s['pct_of_substantive'][c]} |")
    return "\n".join(out)


def compare(summaries):
    cols = [c for c in ORDER if not c.startswith(("M_", "LOW", "TRUNC"))]
    labels = [("+" if c.startswith("P_") else "\u2212") + c[2:].lower() for c in cols]
    head = "| shop | n | " + " | ".join(labels) + " |"
    rule = "|---|---:|" + "---:|" * len(cols)
    rows = [head, rule]
    for s in sorted(summaries, key=lambda x: -x["n"]):
        cells = [f"{s['pct_of_substantive'].get(c, 0):.0f}" for c in cols]
        rows.append(f"| {s['shop']} | {s['n']} | " + " | ".join(cells) + " |")
    return ("Percent of substantive reviews carrying each theme. "
            "P_ columns are drivers, N_ columns are pain points.\n\n" + "\n".join(rows))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=pathlib.Path)
    ap.add_argument("--compare", action="store_true", help="cross-shop percentage table")
    ap.add_argument("--json", action="store_true", help="emit coded records as JSON")
    args = ap.parse_args()

    missing = [f for f in args.files if not f.exists()]
    if missing:
        sys.exit(f"no such corpus file(s): {', '.join(str(m) for m in missing)}")

    summaries = [summarise(f) for f in args.files]
    empty = [s["shop"] for s in summaries if s["n"] == 0]
    if empty:
        sys.exit(f"parsed 0 reviews from: {', '.join(empty)} — "
                 "is the dump in Google Maps pane format? See README.md.")

    if args.json:
        print(json.dumps(summaries, indent=2, ensure_ascii=False))
    elif args.compare:
        print(compare(summaries))
    else:
        print("\n\n".join(report(s) for s in summaries))


if __name__ == "__main__":
    main()
