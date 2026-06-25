#!/usr/bin/env python3
"""
md-housekeeping — read-only audit + opt-in cleanup for markdown files across
deepak's project tree.

Scans 7 known locations for:
  1. True duplicates (same SHA256 content, different paths).
  2. node_modules / build-dump pollution (lots of vendor *.md files).
  3. Stale files (>= STALE_DAYS old, currently 90).
  4. Broken Obsidian-style wikilinks in the vault.

Read-only by default. Pass --apply to interactively delete duplicates.
Pass --apply --yes to delete duplicates without prompting (lexicographically
later path is removed; --yes implies you've reviewed a prior dry run).

Usage:
  python tools/md-housekeeping.py             # dry run, full report
  python tools/md-housekeeping.py --apply     # interactive duplicate cleanup
  python tools/md-housekeeping.py --apply --yes  # non-interactive

The script never touches:
  - files inside node_modules, _work, projects/group9_phase2 (auto-generated)
  - the `superpowers/specs/` copy of any duplicate (canonical location)
  - anything outside the 7 hardcoded LOCATIONS list

Exit codes:
  0 — clean (no findings) or apply succeeded
  1 — findings exist (dry run)
  2 — runtime error
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# ── config ───────────────────────────────────────────────────────────────────

LOCATIONS = [
    ("vault",                 Path(r"C:\PY\vault")),
    ("printosky/docs",        Path(r"C:\PY\printosky\docs")),
    ("printosky/marketing",   Path(r"C:\PY\printosky\marketing")),
    ("osp-academics WIKI",    Path(r"C:\PY\osp-academics\WIKI")),
    ("osp-academics root",    Path(r"C:\PY\osp-academics")),
    ("~/.claude/plans",       Path(r"C:\Users\user\.claude\plans")),
    ("~/.claude/commands",    Path(r"C:\Users\user\.claude\commands")),
]

EXCLUDE_PATH_FRAGMENTS = (
    "node_modules",
    "/_work/",
    "/group9_phase2/",
    "/.git/",
)

VAULT_ROOT = Path(r"C:\PY\vault")
STALE_DAYS = 90
LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


# ── helpers ──────────────────────────────────────────────────────────────────

def is_excluded(p: Path) -> bool:
    s = str(p).replace("\\", "/").lower()
    return any(f in s for f in EXCLUDE_PATH_FRAGMENTS)


def collect_md_files():
    seen = set()
    out = []
    for _, root in LOCATIONS:
        if not root.exists():
            continue
        for f in root.rglob("*.md"):
            if not f.is_file() or is_excluded(f):
                continue
            try:
                resolved = f.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            out.append(f)
    return out


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_canonical(path: Path) -> bool:
    s = str(path).replace("\\", "/")
    return "/superpowers/specs/" in s or "/superpowers/plans/" in s


# ── checks ───────────────────────────────────────────────────────────────────

def find_duplicates(files):
    by_hash = defaultdict(list)
    for f in files:
        try:
            by_hash[sha256_of(f)].append(f)
        except OSError:
            continue
    return {h: paths for h, paths in by_hash.items() if len(paths) > 1}


def find_pollution():
    out = []
    polluted_roots = [
        ("node_modules in pdfsnake",     Path(r"C:\PY\pdfsnake")),
        ("osp-academics _work",          Path(r"C:\PY\osp-academics\_work")),
        ("osp-academics group9_phase2",  Path(r"C:\PY\osp-academics\projects\group9_phase2")),
    ]
    for label, root in polluted_roots:
        if not root.exists():
            continue
        n = sum(1 for _ in root.rglob("*.md"))
        if n:
            out.append((label, n))
    return out


def find_stale(files, days):
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for f in files:
        try:
            mt = datetime.fromtimestamp(f.stat().st_mtime)
        except OSError:
            continue
        if mt < cutoff:
            out.append((f, (datetime.now() - mt).days))
    return sorted(out, key=lambda x: -x[1])


def find_broken_wikilinks():
    if not VAULT_ROOT.exists():
        return []
    md = list(VAULT_ROOT.rglob("*.md"))
    file_basenames = {p.stem.lower(): p for p in md}
    file_relpaths = {
        str(p.relative_to(VAULT_ROOT)).replace("\\", "/").lower().removesuffix(".md"): p
        for p in md
    }

    def norm(s):
        return s.lower().rstrip("/").removesuffix(".md")

    def resolve(src_rel, target):
        tn = norm(target)
        src_dir = "/".join(src_rel.split("/")[:-1])
        if src_dir:
            cand = (src_dir + "/" + tn).lower()
            if cand in file_relpaths:
                return file_relpaths[cand]
        if tn in file_relpaths:
            return file_relpaths[tn]
        base = tn.split("/")[-1]
        if base in file_basenames:
            return file_basenames[base]
        return None

    broken = []
    for f in md:
        rel = str(f.relative_to(VAULT_ROOT)).replace("\\", "/")
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in LINK_RE.finditer(text):
            target = m.group(1).strip()
            if not resolve(rel, target):
                broken.append((rel, target))
    return broken


# ── apply (delete duplicates) ────────────────────────────────────────────────

def pick_keeper_and_loser(paths):
    canonicals = [p for p in paths if is_canonical(p)]
    if canonicals:
        keeper = sorted(canonicals)[0]
    else:
        keeper = sorted(paths)[0]
    losers = [p for p in paths if p != keeper]
    return keeper, losers


def confirm(prompt, auto_yes):
    if auto_yes:
        return True
    sys.stdout.write(prompt + " [y/N] ")
    sys.stdout.flush()
    try:
        return sys.stdin.readline().strip().lower() == "y"
    except (KeyboardInterrupt, EOFError):
        return False


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Markdown housekeeping audit + cleanup.")
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete duplicates (default is dry run).")
    parser.add_argument("--yes", action="store_true",
                        help="Skip per-file prompt. Implies --apply review already done.")
    parser.add_argument("--stale-days", type=int, default=STALE_DAYS,
                        help=f"Stale threshold in days (default: {STALE_DAYS}).")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    files = collect_md_files()
    print(f"Scanned {len(files)} markdown files across {len(LOCATIONS)} locations\n")

    findings = 0

    # 1. Duplicates
    dups = find_duplicates(files)
    print("=" * 76)
    print("## DUPLICATES (same SHA256)")
    print("=" * 76)
    if not dups:
        print("  (none)\n")
    else:
        findings += len(dups)
        for h, paths in sorted(dups.items()):
            keeper, losers = pick_keeper_and_loser(paths)
            size = paths[0].stat().st_size
            print(f"  hash={h[:12]}...  size={size:,}B")
            print(f"    KEEP   {keeper}")
            for l in losers:
                print(f"    REMOVE {l}")
            print()

    # 2. Pollution
    pollution = find_pollution()
    print("=" * 76)
    print("## POLLUTION (vendor / dump *.md files - exclude from indexers)")
    print("=" * 76)
    if not pollution:
        print("  (none)\n")
    else:
        for label, n in pollution:
            print(f"  {n:>4} files  -  {label}")
        print("\n  Action: add path patterns to .gitignore / Obsidian's exclusion list.")
        print("  This script does NOT delete vendor files.\n")

    # 3. Stale
    stale = find_stale(files, args.stale_days)
    print("=" * 76)
    print(f"## STALE (>= {args.stale_days} days, no edit)")
    print("=" * 76)
    if not stale:
        print(f"  (none - no md file untouched for {args.stale_days}+ days)\n")
    else:
        findings += len(stale)
        for p, days in stale:
            print(f"  {days:>4}d  {p}")
        print()

    # 4. Broken wikilinks
    broken = find_broken_wikilinks()
    print("=" * 76)
    print("## BROKEN [[wikilinks]] (vault only)")
    print("=" * 76)
    if not broken:
        print("  (none)\n")
    else:
        findings += len(broken)
        for src, tgt in broken:
            print(f"  [[{tgt}]]  in  {src}")
        print()

    # Apply step
    if args.apply and dups:
        print("=" * 76)
        print("## APPLYING DUPLICATE CLEANUP")
        print("=" * 76)
        deleted = 0
        for h, paths in dups.items():
            keeper, losers = pick_keeper_and_loser(paths)
            for loser in losers:
                if confirm(f"  Delete {loser} ?", args.yes):
                    try:
                        loser.unlink()
                        print(f"    DELETED  {loser}")
                        deleted += 1
                    except OSError as e:
                        print(f"    ERROR    {loser}: {e}")
                else:
                    print(f"    SKIPPED  {loser}")
        print(f"\n  Deleted {deleted} file(s).")

    if args.apply:
        return 0
    return 1 if findings else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
    except Exception as e:
        sys.stderr.write(f"[md-housekeeping] error: {e}\n")
        sys.exit(2)
