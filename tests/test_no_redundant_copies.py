"""
Ratchet: helpers that must have exactly one implementation.

Same idea as tests/test_fail_loud_rule.py — the count may go down, never up.

Each of these was a set of verbatim copies before it was extracted, and two of
them carried a docstring saying so ("Match api/index.py's normalization",
"Deliberately the same permissive parse as store_digest._as_datetime"), which
is what keeping copies in step by hand looks like right up until someone edits
one of them. The PIN derivation is the sharp one: a PIN seeded by staff_setup
on the store PC has to verify in print_server at the counter and in api/index
in the cloud, so a copy that drifts locks staff out of one surface with nothing
to point at.

Re-importing the shared name is fine and expected:

    from pin_crypto import verify_pin as _verify_pin      # good
    def _verify_pin(...): ...                             # what this catches
"""

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Directories that are not the product: vendored skills, agent state, retired
# code kept for reference, and the tests themselves.
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".claude", ".agents",
    "retired", "tests", ".pytest_cache", "venv", ".venv",
}

# helper name -> the module that is allowed to define it
SINGLE_HOME = {
    "hash_pin": "pin_crypto.py",
    "verify_pin": "pin_crypto.py",
    "_hash_pin": "pin_crypto.py",
    "_verify_pin": "pin_crypto.py",
    "epson_ip": "printer_endpoints.py",
    "_epson_ip": "printer_endpoints.py",
    "normalize_phone": "phone_format.py",
    "_normalize_phone": "phone_format.py",
    "as_datetime": "timestamps.py",
    "_as_datetime": "timestamps.py",
    "replace_chillus": "malayalam.py",
    "_replace_chillus": "malayalam.py",
    "split_malayalam_english": "malayalam.py",
    "_split_malayalam_english": "malayalam.py",
}


def _sources():
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        yield path


def _module_level_defs(path):
    """Top-level function names defined in `path`. Nested defs don't count."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_every_production_source_parses():
    """Guard the guard.

    The scanners above skip anything they cannot parse, so a file they cannot
    read is a file they cannot police — a blind spot rather than a failure.
    Five modules here start with a UTF-8 BOM (db_cloud.py, watcher.py,
    api/handlers_admin.py, book_catalog.py, dispatch_render.py), which plain
    utf-8 chokes on and utf-8-sig handles; between them they are a large share
    of the codebase, and reading them as utf-8 silently excluded all five.
    If this fails, the scanners have gone quiet — fix the read, not this test.
    """
    unparseable = []
    for path in _sources():
        try:
            ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            unparseable.append(f"{path.relative_to(ROOT)}: {type(exc).__name__}")
    assert unparseable == [], (
        "these files cannot be parsed, so the duplicate-helper scanners are "
        f"silently skipping them: {unparseable}"
    )


@pytest.mark.parametrize("helper,home", sorted(SINGLE_HOME.items()))
def test_helper_has_exactly_one_definition(helper, home):
    definers = sorted(
        str(p.relative_to(ROOT))
        for p in _sources()
        if helper in _module_level_defs(p)
    )
    assert definers in ([], [home]), (
        f"{helper}() must be defined only in {home}; found it in {definers}. "
        f"Import the shared one instead of redefining it — "
        f"`from {home[:-3]} import {helper.lstrip('_')} as {helper}`."
    )


def test_the_chillu_table_itself_is_not_copied():
    """The map is data, and it was duplicated the same way the code was."""
    owners = []
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "").endswith("CHILLU_MAP") for t in node.targets
            ):
                owners.append(str(path.relative_to(ROOT)))
    assert sorted(set(owners)) == ["malayalam.py"], (
        f"the chillu table must live only in malayalam.py; found it in {owners}"
    )


def test_pin_iteration_count_is_not_respelled():
    """A second literal iteration count is how the three copies drifted apart."""
    offenders = [
        str(p.relative_to(ROOT))
        for p in _sources()
        if p.name != "pin_crypto.py"
        and "pbkdf2_hmac" in p.read_text(encoding="utf-8-sig", errors="ignore")
    ]
    assert offenders == [], (
        f"PBKDF2 must only be called in pin_crypto.py; found calls in {offenders}. "
        f"A second copy of the iteration count is how a staff PIN ends up "
        f"verifying at the counter but not in the cloud."
    )
