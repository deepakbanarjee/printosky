"""
Regression test: db_cloud must import cleanly even if optional Phase-A
modules (routing, pickup_code) are missing from the deployment.

Without this guarantee, a missing file in a Vercel deploy would break
every webhook handler in api/index.py — total prod outage from a single
forgotten file.

The defence is in db_cloud itself: routing.engine and pickup_code are
lazily imported inside update_job_paid; module-top imports must not
reference them.
"""
from __future__ import annotations

import importlib
import sys


def _module_names_to_isolate() -> set[str]:
    """All modules we want to re-import in clean isolation."""
    return {"db_cloud", "routing", "routing.engine", "pickup_code"}


def test_db_cloud_imports_when_routing_and_pickup_code_are_absent(monkeypatch):
    """Simulate a deploy where routing/ and pickup_code.py were not
    bundled. db_cloud must still import — only update_job_paid will
    degrade, and only at call-time, never at import-time.

    Snapshots sys.modules and restores it on teardown so this test
    cannot pollute later tests (which had been failing due to stale
    bindings before this fix)."""
    snapshot = dict(sys.modules)

    try:
        # Remove our targets from the cache so import_module re-runs
        # module-top code under the blocking finder.
        for name in _module_names_to_isolate():
            sys.modules.pop(name, None)

        blocked = {"routing", "routing.engine", "pickup_code"}

        class _BlockFinder:
            def find_spec(self, name, path=None, target=None):  # noqa: ANN001
                if name in blocked:
                    raise ModuleNotFoundError(f"blocked by test: {name}")
                return None

        finder = _BlockFinder()
        monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])

        mod = importlib.import_module("db_cloud")

        assert hasattr(mod, "update_job_paid")
        assert callable(mod.update_job_paid)
        assert hasattr(mod, "insert_job_from_webhook")
        assert callable(mod.insert_job_from_webhook)
    finally:
        # Restore sys.modules so subsequent tests see the original
        # bindings they imported at module-load time.
        sys.modules.clear()
        sys.modules.update(snapshot)


def test_db_cloud_module_top_does_not_import_routing_or_pickup():
    """Belt-and-braces: scan db_cloud.py for module-top imports of the
    optional modules. Module-top imports of routing.* or pickup_code
    would re-introduce the failure mode this test guards against."""
    import os
    db_cloud_path = os.path.join(os.path.dirname(__file__), "..", "db_cloud.py")
    with open(db_cloud_path, encoding="utf-8") as fh:
        src = fh.read()
    forbidden_patterns = (
        "\nimport routing",
        "\nfrom routing",
        "\nimport pickup_code",
        "\nfrom pickup_code",
    )
    for pat in forbidden_patterns:
        assert pat not in "\n" + src, (
            f"db_cloud.py has a module-top import matching {pat!r}; "
            "this would break every db_cloud importer if the module is "
            "missing in deployment. Move it inside the function instead."
        )
