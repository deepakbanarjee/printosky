"""
The deploy table has to be true.

docs/ARCHITECTURE.md named `sprint/session-9` as Netlify's source long after
that branch was deleted. A stale deploy target is worse than no deploy doc: it
sends you looking for a branch problem when a console looks out of date, instead
of at the build or the cache. Established 2026-08-19 that everything ships from
`main` — `store-diag`, `jobs.html` and `dtp.html` are live and exist only there.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ARCH = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
CLAUDE_MD = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def _deploy_section() -> str:
    m = re.search(r"## Deploy Pipeline(.+?)\n---", ARCH, re.S)
    assert m, "ARCHITECTURE.md lost its Deploy Pipeline section"
    return m.group(1)


def test_no_sprint_branch_is_named_as_a_deploy_source():
    """Sprint branches are deleted when the sprint ends; naming one as a deploy
    target guarantees the doc goes stale. The only exception is the note
    explaining that this exact mistake was made."""
    section = _deploy_section()
    for line in section.splitlines():
        if line.lstrip().startswith(">"):
            continue                      # the historical note may name it
        assert "sprint/session" not in line, f"stale deploy branch in: {line.strip()}"


@pytest.mark.parametrize("platform", ["Vercel", "Netlify"])
def test_both_platforms_are_documented_as_building_from_main(platform):
    section = _deploy_section()
    row = [l for l in section.splitlines() if platform in l]
    assert row, f"{platform} missing from the deploy table"
    assert "`main`" in row[0], f"{platform} row does not say main: {row[0].strip()}"


def test_the_manual_step_is_called_out():
    """Vercel and Netlify update themselves; store PCs do not. Forgetting that
    is how a fix looks deployed while the counter still runs the old code."""
    section = _deploy_section()
    assert "Store PCs" in section
    assert "manual" in section.lower()
    assert "PULL_UPDATE.bat" in section


def test_claude_md_agrees():
    assert "branch `main`" in CLAUDE_MD
    assert "Netlify" in CLAUDE_MD, "CLAUDE.md lists Vercel and Supabase but not the console host"
