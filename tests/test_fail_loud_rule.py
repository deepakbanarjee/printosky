"""
Enforcement for the fail-loud rule (docs/FAIL_LOUD.md).

The rule: if something that is expected to work stops working, a human is told.
The enemy is the silent handler —

    try:
        ...
    except Exception:
        pass

— which is how Nattika's printer pipeline stayed dead for a week. There are 80-odd
of these in the codebase and this change does not pretend to have audited them
all. What it does is ratchet: the count per file may go DOWN, never up. Adding a
new silent swallow fails this test, and the fix is one line —

    with ops_watchdog.guard("thing.that.can.break", reraise=False):
        ...

If you genuinely need a new silent handler (best-effort cleanup on a path that
cannot matter — closing a connection, a truly optional import), raise that file's
budget here in the same commit and say why in the message. That is the whole
point: it becomes a visible decision rather than a default.
"""

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Silent-handler budget per file, frozen 2026-08-18. LOWER these freely; raising
# one requires a reason in the commit message.
BUDGET = {
    "academic_pipeline_worker.py": 1,
    "book_bot.py": 8,
    "colour_detector.py": 1,
    "dashboard.py": 4,
    "db_cloud.py": 2,
    "epson_jobs_fetcher.py": 1,
    "epson_snmp_discover.py": 1,
    "job_tracker.py": 1,
    "ops_watchdog.py": 1,          # closing a SQLite handle; nothing to report
    "pdf_scanner.py": 5,
    "print_planner.py": 2,
    "print_server.py": 3,   # 5 -> 3: /file's two silent filepath reads went
                            # when resolution moved into _resolve_job_file (2026-08-30)
    "printer_poller.py": 6,
    "rate_card.py": 3,
    "session_timeout.py": 1,
    "staff_setup.py": 1,
    "store_config.py": 2,
    "store_puller.py": 5,
    "watcher.py": 9,
    "webhook_receiver.py": 1,
    "whatsapp_bot.py": 3,
    "whatsapp_notify.py": 3,
    "work_session_tracker.py": 1,
    "api/handlers_admin.py": 4,
    "api/handlers_pb.py": 1,
    "api/index.py": 8,
    "api/inngest.py": 2,
}

# Modules that carry the store's live pipelines. A failure in any of these is
# invisible to everyone until someone opens the console, so each must be wired
# to the watchdog.
MUST_REPORT = [
    "printer_poller.py",
    "epson_jobs_fetcher.py",
    "supabase_sync.py",
    "print_server.py",
]


def _silent_handlers(path: pathlib.Path) -> int:
    """Handlers whose entire body is `pass` (docstring-only counts too)."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body = [s for s in node.body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        if not body or (len(body) == 1 and isinstance(body[0], ast.Pass)):
            n += 1
    return n


def _python_files():
    files = sorted(ROOT.glob("*.py")) + sorted((ROOT / "api").glob("*.py"))
    return [f for f in files if f.name != "conftest.py"]


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_silent_handlers_do_not_increase(path):
    rel = str(path.relative_to(ROOT))
    budget = BUDGET.get(rel, 0)
    found = _silent_handlers(path)
    assert found <= budget, (
        f"{rel} has {found} silent `except: pass` handlers, budget {budget}.\n"
        f"Wrap the new one in ops_watchdog.guard(...) so the failure is reported, "
        f"or raise the budget in tests/test_fail_loud_rule.py and say why."
    )


def test_budget_has_no_stale_entries():
    """A budget higher than reality is a ratchet that has stopped ratcheting."""
    loose = {}
    for rel, budget in BUDGET.items():
        path = ROOT / rel
        if not path.exists():
            continue
        found = _silent_handlers(path)
        if found < budget:
            loose[rel] = (found, budget)
    assert not loose, (
        "These files now have fewer silent handlers than their budget — lower the "
        f"budget to lock the win in: {loose}"
    )


@pytest.mark.parametrize("module", MUST_REPORT)
def test_live_pipelines_report_their_health(module):
    src = (ROOT / module).read_text(encoding="utf-8-sig")
    assert "ops_watchdog" in src, (
        f"{module} runs a live pipeline but never reports to ops_watchdog. "
        "A failure there is invisible until someone opens the console."
    )


def test_the_rule_is_written_down():
    doc = (ROOT / "docs" / "FAIL_LOUD.md").read_text(encoding="utf-8")
    assert "ops_watchdog" in doc
    for section in ("The rule", "How to comply", "Nattika"):
        assert section in doc, f"docs/FAIL_LOUD.md lost its '{section}' section"
    claude_md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "FAIL_LOUD.md" in claude_md, "the rule must be reachable from CLAUDE.md"
