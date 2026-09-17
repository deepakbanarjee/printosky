"""
The API dispatch chain's ordering is load-bearing. This test enforces it.

api/index.py routes by walking a flat `if self.path == ...` / `if
self.path.startswith(...)` chain — 56 tests in do_GET and 55 in do_POST, tried
in source order, first match wins. Nothing about that is wrong today: no route
is currently unreachable. What is missing is anything stopping it.

A prefix test swallows every later route underneath it. Adding
`/admin/book-orders` above `/admin/book-orders/<code>/confirm` silently routes
the confirm to the list handler — the request still returns 200, just from the
wrong handler, so it fails as wrong data rather than as an error. That has been
hit here at least once: the guard comment above the book-orders route
("Exact path (+ optional query) only, so a GET to a sub-route like
/admin/book-orders/<code>/confirm is not swallowed by the list handler") is the
scar.

The rule this pins: no route may be unreachable because an earlier one already
matches everything it matches. Order the chain most-specific-first, or make the
earlier test exact.
"""

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = ROOT / "api" / "index.py"


def _handler_class(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "handler":
            return node
    raise AssertionError("api/index.py no longer defines `class handler`")


def _path_tests(fn):
    """[(lineno, kind, literal)] for each top-level self.path test, in order.

    kind is "exact" for `self.path == "..."` / `self.path in (...)`, and
    "prefix" for `self.path.startswith("...")`. Anything else — a regex match,
    a helper call — is not a plain literal claim on the path space and is
    skipped; those cannot mask a later route by string prefix alone.
    """
    out = []
    for stmt in fn.body:
        if not isinstance(stmt, ast.If):
            continue
        for node in ast.walk(stmt.test):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "startswith"
                and node.args
            ):
                try:
                    out.append((stmt.lineno, "prefix", ast.literal_eval(node.args[0])))
                except ValueError:
                    pass
            elif (
                isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Attribute)
                and node.left.attr == "path"
            ):
                for op, cmp in zip(node.ops, node.comparators):
                    try:
                        value = ast.literal_eval(cmp)
                    except ValueError:
                        continue
                    if isinstance(op, ast.Eq):
                        out.append((stmt.lineno, "exact", value))
                    elif isinstance(op, ast.In) and isinstance(value, (list, tuple)):
                        for item in value:
                            out.append((stmt.lineno, "exact", item))
    return out


def _methods():
    tree = ast.parse(API.read_text(encoding="utf-8-sig"))
    handler = _handler_class(tree)
    return {
        node.name: node
        for node in handler.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("do_")
    }


METHODS = sorted(_methods())


@pytest.mark.parametrize("method", METHODS)
def test_no_route_is_shadowed_by_an_earlier_one(method):
    routes = _path_tests(_methods()[method])
    seen = []
    shadowed = []
    for lineno, kind, path in routes:
        for prev_line, prev_kind, prev_path in seen:
            masked = (
                path.startswith(prev_path) if prev_kind == "prefix"
                else path == prev_path
            )
            if masked:
                shadowed.append(
                    f"{method} line {lineno} ({kind} {path!r}) is unreachable — "
                    f"line {prev_line} ({prev_kind} {prev_path!r}) matches it first"
                )
        seen.append((lineno, kind, path))
    assert not shadowed, "\n".join(shadowed)


def test_the_chain_is_actually_being_checked():
    """Guard the guard: if the parse silently stops finding routes, say so."""
    counts = {m: len(_path_tests(_methods()[m])) for m in METHODS}
    assert counts.get("do_GET", 0) >= 40, counts
    assert counts.get("do_POST", 0) >= 40, counts
