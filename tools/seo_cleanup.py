"""One-off SEO hygiene cleanup for the printosky.com static site (website/).

- Adds self-referencing <link rel="canonical"> to public pages missing one.
- Adds <meta name="robots" content="noindex, nofollow"> to internal/thin pages
  that must not be indexed (admin consoles, thank-you/utility pages, dev versions).
- Rebuilds sitemap.xml with the correct public URL set and fresh lastmod dates.

Idempotent: re-running makes no further changes. Inserts after </title>.
Leaves already-correct pages and the Google verification file untouched.
"""
from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "website"
BASE = "https://printosky.com"

# Public, indexable pages currently missing a canonical tag.
CANONICAL = ["academic", "books", "project-builder"]

# Internal / thin / dev pages that must NOT be indexed.
NOINDEX = [
    "superadmin", "operator-mode", "chat",      # internal admin consoles
    "cv-builder",                               # operator-only during private testing, not public yet
    "payment-done", "pb-retrieve", "notes",     # thank-you / utility / dead redirect
    "project-builder-v2", "project-builder-v3", "project-builder-v4",  # dev iterations
]

TITLE_RE = re.compile(r"^(\s*)(.*</title>)", re.IGNORECASE)


def insert_after_title(text: str, tag: str) -> str | None:
    """Insert `tag` on its own line right after the </title> line, matching indent.
    Returns new text, or None if no </title> found."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = TITLE_RE.search(line)
        if m:
            indent = m.group(1)
            if not line.endswith("\n"):
                lines[i] = line + "\n"
            lines.insert(i + 1, f"{indent}{tag}\n")
            return "".join(lines)
    return None


def apply_canonical(name: str) -> str:
    f = WEB / f"{name}.html"
    text = f.read_text(encoding="utf-8")
    if re.search(r'rel="canonical"', text, re.IGNORECASE):
        return f"  skip (already has canonical): {name}.html"
    tag = f'<link rel="canonical" href="{BASE}/{name}.html">'
    new = insert_after_title(text, tag)
    if new is None:
        return f"  ERROR no </title>: {name}.html"
    f.write_text(new, encoding="utf-8")
    return f"  + canonical -> {name}.html"


def apply_noindex(name: str) -> str:
    f = WEB / f"{name}.html"
    text = f.read_text(encoding="utf-8")
    if re.search(r'name="robots"[^>]*noindex', text, re.IGNORECASE):
        return f"  skip (already noindex): {name}.html"
    tag = '<meta name="robots" content="noindex, nofollow">'
    new = insert_after_title(text, tag)
    if new is None:
        return f"  ERROR no </title>: {name}.html"
    f.write_text(new, encoding="utf-8")
    return f"  + noindex   -> {name}.html"


# (loc, lastmod, changefreq, priority) for the public sitemap.
SITEMAP = [
    ("/",                     "2026-06-14", "weekly",  "1.0"),
    ("/services.html",        "2026-06-14", "monthly", "0.9"),
    ("/order-v2.html",        "2026-06-16", "monthly", "0.9"),
    ("/books.html",           "2026-06-16", "monthly", "0.8"),
    ("/about.html",           "2026-06-14", "yearly",  "0.7"),
    ("/contact.html",         "2026-06-14", "yearly",  "0.7"),
    ("/academic.html",        "2026-06-16", "monthly", "0.6"),
    # cv-builder.html omitted from sitemap: operator-only during private testing.
    ("/project-builder.html", "2026-06-16", "monthly", "0.6"),
    ("/account.html",         "2026-06-16", "monthly", "0.5"),
]


def build_sitemap() -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod, freq, pri in SITEMAP:
        out += ["  <url>",
                f"    <loc>{BASE}{loc}</loc>",
                f"    <lastmod>{lastmod}</lastmod>",
                f"    <changefreq>{freq}</changefreq>",
                f"    <priority>{pri}</priority>",
                "  </url>"]
    out.append("</urlset>")
    return "\n".join(out) + "\n"


def main() -> None:
    print("== canonical ==")
    for n in CANONICAL:
        print(apply_canonical(n))
    print("== noindex ==")
    for n in NOINDEX:
        print(apply_noindex(n))
    print("== sitemap ==")
    (WEB / "sitemap.xml").write_text(build_sitemap(), encoding="utf-8")
    print(f"  rewrote sitemap.xml ({len(SITEMAP)} urls)")


if __name__ == "__main__":
    main()
