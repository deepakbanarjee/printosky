"""storage_cleanup.py — reclaim Supabase Storage in the incoming-files bucket.

Why: the free-tier storage cap (1 GB) and the project's Disk IO budget are both
under pressure from files that no live job references any more — WhatsApp intake
that never became an order, project-builder outputs from May, and media the bot
has already delivered to the customer.

Safety model — this deletes customer files, so it is deliberately conservative:

* **Dry-run by default.** Nothing is deleted without ``--apply``.
* **Never touches a referenced file.** Any object whose public URL appears in
  ``jobs.file_url`` is excluded, whatever its age or tier.
* **Never touches payment evidence.** ``book-payments/`` is excluded outright —
  it is accounting proof and only ~5 MB.
* **Age-gated.** A file must be older than the tier's minimum age to qualify.
* **Manifest first.** ``--apply`` writes a CSV of everything it is about to
  remove before removing it, so a deletion can always be audited after the fact.

Usage:
    python tools/storage_cleanup.py                 # dry run, all tiers
    python tools/storage_cleanup.py --tier A        # dry run, one tier
    python tools/storage_cleanup.py --apply         # delete (writes manifest)
    python tools/storage_cleanup.py --apply --tier A --min-age-days 120
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import os
import sys

BUCKET = "incoming-files"

# Prefixes that must never be swept, regardless of age or tier.
PROTECTED_PREFIXES = ("book-payments/",)

# Tier definitions: prefix to match, minimum age in days, and what it is.
# `prefix=None` means "a bare intake file at the bucket root" (the
# <phone>_<timestamp>_<name> uploads WhatsApp capture writes).
TIERS = {
    "A": {
        "prefix": "project-builder/",
        "min_age_days": 90,
        "what": "project-builder outputs (job delivered)",
    },
    "B": {
        "prefix": "outbound/",
        "min_age_days": 30,
        "what": "bot media already sent to the customer",
    },
    "C": {
        "prefix": None,
        "min_age_days": 60,
        "what": "orphaned WhatsApp intake that never became an order",
    },
}


def _client():
    """Build a Supabase client from the environment (service key required)."""
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except Exception:
        pass
    from supabase import create_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
    return create_client(url, key)


def object_key(url: str) -> str:
    """Reduce a storage URL to the bucket-relative object key.

    Job rows do not store a canonical URL. Observed variations include a
    trailing '?' (the common case — roughly a third of rows), percent-encoded
    spaces, and signed-URL query strings. Comparing raw URL strings therefore
    reports a live file as unreferenced, which is exactly how the first run of
    this script deleted 49 files that Pending jobs still pointed at.

    Normalising to the key — strip the query string, then URL-decode — makes
    both sides comparable regardless of which form the row happens to carry.
    """
    from urllib.parse import unquote

    if not url:
        return ""
    key = url.split("?", 1)[0]
    marker = f"/{BUCKET}/"
    idx = key.find(marker)
    if idx != -1:
        key = key[idx + len(marker):]
    return unquote(key).strip().lstrip("/")


def referenced_keys(sb) -> tuple[set[str], set[str]]:
    """Object keys, and bare filenames, referenced by any job row.

    Returns (keys, basenames). The basename set is a deliberately blunt second
    guard: if a job row references a file whose key we somehow fail to match,
    the filename alone is still enough to veto the delete. It costs a few
    genuinely-orphaned files that happen to share a name with a live job — an
    acceptable trade against deleting a customer's file.

    Fetched in pages because PostgREST caps a single response, and a missed row
    here would mean deleting a live customer file.
    """
    keys: set[str] = set()
    names: set[str] = set()
    page, size = 1000, 1000
    offset = 0
    while True:
        res = (
            sb.table("jobs")
            .select("file_url,filename")
            .range(offset, offset + page - 1)
            .execute()
        )
        rows = res.data or []
        for r in rows:
            key = object_key((r.get("file_url") or "").strip())
            if key:
                keys.add(key)
                names.add(key.rsplit("/", 1)[-1])
            fn = (r.get("filename") or "").strip()
            if fn:
                names.add(fn)
        if len(rows) < page:
            break
        offset += page
    return keys, names


def list_objects(sb) -> list[dict]:
    """List every object in the bucket, recursing into folders.

    The storage list API is NOT recursive: listing a path returns that level's
    files plus *folder markers* — entries with a null ``id`` and no metadata.
    Listing only the root therefore sees the bare intake uploads and none of the
    foldered objects, so tiers keyed on a prefix (project-builder/, outbound/)
    silently match nothing and the sweep under-reports.

    Each level is paged, and returned names are rewritten to the full path from
    the bucket root so classify() and the delete call both address the real key.
    """
    out: list[dict] = []
    queue: list[str] = [""]
    visited: set[str] = set()

    while queue:
        prefix = queue.pop()
        if prefix in visited:
            continue
        visited.add(prefix)

        offset, limit = 0, 100
        while True:
            batch = sb.storage.from_(BUCKET).list(
                path=prefix, options={"limit": limit, "offset": offset}
            ) or []
            for entry in batch:
                name = entry.get("name") or ""
                if not name:
                    continue
                full = f"{prefix}/{name}" if prefix else name
                # A folder marker carries no id/metadata — descend into it
                # rather than treating it as a deletable object.
                if entry.get("id") is None:
                    queue.append(full)
                else:
                    out.append({**entry, "name": full})
            if len(batch) < limit:
                break
            offset += limit
    return out


def _age_days(created: str | None) -> float:
    if not created:
        return 0.0
    try:
        ts = _dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (_dt.datetime.now(_dt.timezone.utc) - ts).total_seconds() / 86400.0


def classify(name: str, created: str | None, size: int,
             referenced: set[str], base_url: str,
             tiers: list[str], min_age_override: int | None,
             referenced_names: set[str] | None = None) -> str | None:
    """Return the tier a file qualifies for, or None to keep it.

    ``referenced`` holds normalised object keys (see object_key). ``base_url``
    is accepted for signature compatibility but is no longer used to rebuild a
    URL for comparison — that reconstruction was the source of the false
    "unreferenced" verdicts.
    """
    if any(name.startswith(p) for p in PROTECTED_PREFIXES):
        return None

    key = object_key(name)
    if key in referenced:
        return None  # a job still points at this file

    # Second, independent guard: match on filename alone. Catches any row whose
    # URL form we failed to normalise.
    if referenced_names and key.rsplit("/", 1)[-1] in referenced_names:
        return None

    age = _age_days(created)
    for tier in tiers:
        spec = TIERS[tier]
        prefix = spec["prefix"]
        min_age = min_age_override if min_age_override is not None else spec["min_age_days"]
        if prefix is None:
            # Tier C: bucket-root intake only — never a foldered object.
            if "/" in name:
                continue
        elif not name.startswith(prefix):
            continue
        if age >= min_age:
            return tier
    return None


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default is a dry run)")
    ap.add_argument("--tier", action="append", choices=sorted(TIERS),
                    help="limit to a tier (repeatable); default is all")
    ap.add_argument("--min-age-days", type=int, default=None,
                    help="override each tier's minimum age")
    ap.add_argument("--manifest", default=None,
                    help="path for the deletion manifest CSV")
    args = ap.parse_args(argv)

    tiers = args.tier or sorted(TIERS)

    try:
        sb = _client()
    except KeyError as e:
        print(f"error: missing environment variable {e}", file=sys.stderr)
        return 2

    base_url = os.environ["SUPABASE_URL"].rstrip("/")

    print(f"Scanning {BUCKET} …")
    referenced, referenced_names = referenced_keys(sb)
    objects = list_objects(sb)
    print(f"  {len(objects)} objects, {len(referenced)} referenced by a job row")

    # Sanity gate. If the reference set comes back empty (or absurdly small)
    # while the bucket is full, something is wrong with the query or the URL
    # normalisation — and proceeding would treat every live file as an orphan.
    # Refuse rather than risk a repeat of the 49-file deletion.
    if objects and len(referenced) < max(1, len(objects) // 20):
        print(
            f"\nrefusing to continue: only {len(referenced)} referenced keys for "
            f"{len(objects)} objects — the reference lookup looks broken.",
            file=sys.stderr,
        )
        return 3
    print()

    doomed: list[tuple[str, str, int, float]] = []
    for obj in objects:
        name = obj.get("name") or ""
        meta = obj.get("metadata") or {}
        size = int(meta.get("size") or 0)
        created = obj.get("created_at")
        tier = classify(name, created, size, referenced, base_url,
                        tiers, args.min_age_days, referenced_names)
        if tier:
            doomed.append((tier, name, size, _age_days(created)))

    if not doomed:
        print("Nothing qualifies for cleanup.")
        return 0

    doomed.sort(key=lambda r: (-r[2]))
    by_tier: dict[str, list] = {}
    for tier, name, size, age in doomed:
        by_tier.setdefault(tier, []).append((name, size, age))

    total = 0
    for tier in sorted(by_tier):
        rows = by_tier[tier]
        sub = sum(s for _n, s, _a in rows)
        total += sub
        print(f"Tier {tier} — {TIERS[tier]['what']}")
        print(f"  {len(rows)} files, {_human(sub)}")
        for name, size, age in rows[:5]:
            print(f"    {_human(size):>9}  {age:5.0f}d  {name[:70]}")
        if len(rows) > 5:
            print(f"    … and {len(rows) - 5} more")
        print()

    print(f"TOTAL: {len(doomed)} files, {_human(total)}")

    if not args.apply:
        print("\nDRY RUN — nothing deleted. Re-run with --apply to delete.")
        return 0

    manifest = args.manifest or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"storage_cleanup_{_dt.datetime.now():%Y%m%d_%H%M%S}.csv",
    )
    with open(manifest, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["tier", "name", "size_bytes", "age_days"])
        for tier, name, size, age in doomed:
            w.writerow([tier, name, size, f"{age:.1f}"])
    print(f"\nManifest written: {manifest}")

    names = [name for _t, name, _s, _a in doomed]
    removed = 0
    for i in range(0, len(names), 100):
        chunk = names[i:i + 100]
        try:
            sb.storage.from_(BUCKET).remove(chunk)
            removed += len(chunk)
            print(f"  deleted {removed}/{len(names)}")
        except Exception as e:
            print(f"  batch failed ({len(chunk)} files): {e}", file=sys.stderr)

    print(f"\nDone — {removed} files removed, {_human(total)} reclaimed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
