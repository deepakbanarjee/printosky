# Retired 2026-05-12 — graveyard wave 1

Three features from [TASK-014 graveyard triage][triage] retired in one wave. They were either non-functional, unused, or both, and the owner decision was "park them — they may be revived later." This folder is the park.

[triage]: ../../../vault/printosky/feature-graveyard-triage-2026-05.md  (in the Obsidian vault, not the repo)

## What's here

| File | Replaces | Original location | Why retired |
|---|---|---|---|
| `b2b_bot.py` | B2B WhatsApp bot dispatcher | repo root | 0 b2b_clients rows in production; no clear owner |
| `b2b_manager.py` | B2B client + invoice + credit-limit logic | repo root | (same B2B feature) |
| `konica_attribution.py` | `KONICA_USER_PC_MAP` dict + `attribute_konica_jobs()` | `print_server.py` | 0/4507 attribution success rate; four root-cause failures documented in the file header |
| `receipt_printer.py` | `RECEIPT_PRINTER` constant + `handle_print_receipt()` + `POST /print-receipt` route | `print_server.py` | Hardware was never purchased; stub returned "not configured" on every call |

## Live production state (frozen, not destroyed)

These features wrote data on the way out. Tables remain in live Supabase (`mlhuwlnwwwxdnqafelko`) — schema preserved, data preserved, just no longer receiving writes:

| Table | Rows at retirement | Notes |
|---|---|---|
| `b2b_clients` | 0 | empty since launch |
| `b2b_payments` | 0 | empty since launch |
| `konica_jobs.attributed_to` | 4507/4507 NULL | column kept; attribution column unwritten |

The schema doc at [`docs/SCHEMA.md`](../../docs/SCHEMA.md) still lists these tables / columns. When a feature is properly retired (not just paused), the migration to drop the columns/tables is a separate decision — owner sign-off required.

## How to revive (general pattern)

1. Read the file header in this folder — each retired file documents *why* it died and *what would need to change* to bring it back.
2. Copy the file back to its original location (or somewhere sensible).
3. Restore the call sites:
   - **B2B:** revert the `_B2B_RETIRED_MSG` stubs in `watcher.py` to import from `b2b_manager` / `b2b_bot` again. Restore the inner-function imports at the message-handler branches and the `setup_b2b_db(DB_PATH)` call.
   - **Konica attribution:** restore the `from print_server import attribute_konica_jobs, KONICA_USER_PC_MAP` block in `supabase_sync.py:sync_once()`. Fix the case-sensitivity bug noted in the file header before re-enabling.
   - **Receipt printer:** restore the `POST /print-receipt` route block in `print_server.py`'s request handler. Set `RECEIPT_PRINTER` to the real `{vendor, product}` dict. Uncomment the `python-escpos` lines.
4. Run the test suite (`pytest tests/`) before pushing — the original test files (`test_b2b.py`, `test_b2b_manager.py`) are still in place and will exercise the revived code.

## Commit reference

The retirement is a single commit on branch `claude/retire-graveyard-2026-05` off main. To find the diff:

```
git log --oneline --diff-filter=R -- retired/   # the b2b file renames
git log --oneline -- retired/2026-05-12-graveyard/
git show <commit-sha> -- print_server.py watcher.py supabase_sync.py
```

## Not retired in this wave

Items 1, 3, 5, 7, 8 from the triage stayed in place:

- **MIS dashboard** (item 1) — UI works; the data dependency is upstream.
- **`session_timeout.py`** (item 3) — code is in place; verify when PIN login resumes.
- **A3 printing** (item 5) — needs a live-test from a phone, not retirement.
- **Konica toner % parsing** (item 7) — found to actually be working (51k rows of fresh data); was a triage false alarm.
- **`finishing=thermal` rate** (item 8) — needs a live-test, then maybe remove from rate card.

The corrected verdicts live in the triage vault note linked above.
