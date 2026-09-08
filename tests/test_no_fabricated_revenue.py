"""A console with no data says so. It does not invent a day's takings.

Both consoles used to hold `DEMO_JOBS` and
`DEMO_SUMMARY = { total_jobs:4, completed:2, pending:2, revenue:140, cash:65,
upi:75 }`, and showed them whenever the jobs query returned zero rows, labelled
"Demo Mode — start watcher at store to see live data".

Zero rows has three quite different causes and that label named the wrong one
for two of them:

1. **Nobody signed in** — the common case. `sbFetch` falls back to the anon key
   when `sessionStorage.supabase_jwt` is absent; RLS (`auth.role() =
   'authenticated'`, SEC-4) then allows nothing and Supabase answers **200 with
   `[]`**, not 401, so the auth path never fires. PRIOFF sat exactly like this
   on 2026-09-06 with **135 real jobs** in the table.
2. A store that genuinely has no jobs yet — PRINTK has none, ever (S9-9).
3. The table actually being empty.

None of those is a reason to draw four jobs and ₹140 of revenue on a screen
staff are meant to trust. It is the same family as MIS showing February–March
data for five months and looking plausible throughout: a number computed over
nothing, rendered as if it were counted.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

CONSOLES = ("jobs.html", "admin.html")


def _src(name: str) -> str:
    return (ROOT / "website" / name).read_text(encoding="utf-8")


def _code(name: str) -> str:
    """Source with pure `//` comment lines dropped.

    The comment left where the demo data was names it on purpose, so anyone
    grepping for the fault finds the explanation. A mention in prose is not a
    use, and a check that cannot tell the two apart fails on its own docs.
    """
    return "\n".join(l for l in _src(name).splitlines() if not l.strip().startswith("//"))


@pytest.mark.parametrize("name", CONSOLES)
def test_the_console_holds_no_invented_jobs_or_money(name):
    code = _code(name)
    for banned in ("DEMO_JOBS", "DEMO_SUMMARY"):
        assert banned not in code, (
            f"{name} can render fabricated jobs again. Whatever the query "
            "returns, a console must not draw rows and revenue nobody counted."
        )
    assert "revenue:140" not in code.replace(" ", ""), (
        f"{name} carries a hardcoded revenue figure"
    )


@pytest.mark.parametrize("name", CONSOLES)
def test_empty_is_told_apart_from_signed_out(name):
    """The distinction is the whole fix: one is 'nothing happened yet', the
    other is 'you are seeing nothing because you are not authenticated'."""
    code = _code(name)
    assert "function emptyReason()" in code, f"{name} lost the reason check"
    assert "supabase_jwt" in code, (
        f"{name} no longer looks at the session token, so it cannot tell an "
        "empty store from a signed-out one"
    )
    assert 'setSyncState(hasJobs ? "ok" : emptyReason());' in code


@pytest.mark.parametrize("name", CONSOLES)
def test_the_label_names_the_real_cause(name):
    """"Demo Mode — start watcher at store" pointed at the wrong thing for the
    two commonest causes, and told staff to restart a service that was fine."""
    src = _src(name)
    assert 'Not signed in' in src, f"{name} does not say when you are signed out"
    assert 'no jobs yet' in src, f"{name} does not distinguish a genuinely empty store"
    label_block = src[src.index("function setSyncState("):]
    label_block = label_block[:label_block.index("\n}")]
    assert "Demo Mode" not in label_block, (
        f"{name} still labels an empty result as demo mode"
    )


@pytest.mark.parametrize("name", CONSOLES)
def test_the_stats_are_always_the_real_ones(name):
    code = _code(name)
    assert "renderStats(summarizeJobs(todaysJobs, storeFilter));" in code
    assert re.search(r"renderStats\(\s*\w*Demo", code) is None, (
        f"{name} can still hand renderStats something other than the real rows"
    )


@pytest.mark.parametrize("name", CONSOLES)
def test_the_job_list_is_whatever_the_query_returned(name):
    code = _code(name)
    assert "allJobs = jobs;" in code, (
        f"{name} substitutes something for the query result again"
    )


def test_both_consoles_got_the_same_treatment():
    """Drift between these two files is a known problem in this repo — S13-10b
    added character-for-character comparisons for exactly that reason."""
    def block(name):
        s = _src(name)
        i = s.index("// ── Empty is not the same as unauthenticated")
        return s[i:s.index("\n}", i)]
    assert block("jobs.html") == block("admin.html")


def test_the_checks_above_run_against_real_files():
    """A parametrised suite over missing files passes silently. Three faults in
    this run were a green light computed over nothing; state the inputs."""
    for name in CONSOLES:
        assert (ROOT / "website" / name).exists(), name
        assert len(_src(name)) > 10_000, f"{name} is too small to be the console"
