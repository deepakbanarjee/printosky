"""
TASK-015 (roadmap-2026-05): unit tests for scripts/check_env.py.

Covers the pure check_target() validator with synthetic env dicts — no real
secrets, no Supabase, no network. Also asserts the shipped manifest parses and
that the real .env (when present) satisfies the store_pc target.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pytest

import check_env  # noqa: E402

MANIFEST = json.loads((ROOT / "config" / "env_manifest.json").read_text(encoding="utf-8"))


# ═════════════════════════════════════════════════════════════════════════════
# check_target — required vars
# ═════════════════════════════════════════════════════════════════════════════

class TestRequired:
    SPEC = {
        "required": [
            {"name": "A", "pattern": r"^\d+$"},
            {"name": "B", "pattern": r"^rzp_(live|test)_.+$"},
        ],
        "optional": [],
    }

    def test_all_present_valid(self) -> None:
        errors, warnings = check_env.check_target("t", self.SPEC,
                                                  {"A": "123", "B": "rzp_live_abc"})
        assert errors == []
        assert warnings == []

    def test_missing_required(self) -> None:
        errors, _ = check_env.check_target("t", self.SPEC, {"A": "123"})
        assert any("MISSING required B" in e for e in errors)

    def test_empty_string_is_missing(self) -> None:
        errors, _ = check_env.check_target("t", self.SPEC, {"A": "", "B": "rzp_test_x"})
        assert any("MISSING required A" in e for e in errors)

    def test_malformed_required(self) -> None:
        errors, _ = check_env.check_target("t", self.SPEC,
                                           {"A": "12x", "B": "rzp_live_x"})
        assert any("MALFORMED A" in e for e in errors)

    def test_wrong_prefix_malformed(self) -> None:
        errors, _ = check_env.check_target("t", self.SPEC,
                                           {"A": "1", "B": "sk_live_x"})
        assert any("MALFORMED B" in e for e in errors)


# ═════════════════════════════════════════════════════════════════════════════
# check_target — optional vars (warn only when present + malformed)
# ═════════════════════════════════════════════════════════════════════════════

class TestOptional:
    SPEC = {
        "required": [],
        "optional": [{"name": "OPT", "pattern": r"^\d+$"}],
    }

    def test_absent_optional_is_fine(self) -> None:
        errors, warnings = check_env.check_target("t", self.SPEC, {})
        assert errors == [] and warnings == []

    def test_present_valid_optional_is_fine(self) -> None:
        errors, warnings = check_env.check_target("t", self.SPEC, {"OPT": "9"})
        assert errors == [] and warnings == []

    def test_present_malformed_optional_warns_not_errors(self) -> None:
        errors, warnings = check_env.check_target("t", self.SPEC, {"OPT": "nope"})
        assert errors == []
        assert any("MALFORMED optional OPT" in w for w in warnings)


# ═════════════════════════════════════════════════════════════════════════════
# Shipped manifest sanity
# ═════════════════════════════════════════════════════════════════════════════

class TestManifest:
    def test_three_targets_present(self) -> None:
        for target in ("vercel", "store_pc", "netlify"):
            assert target in MANIFEST
            assert "required" in MANIFEST[target]

    def test_every_entry_has_name_and_compilable_pattern(self) -> None:
        import re
        for target in ("vercel", "store_pc", "netlify"):
            for bucket in ("required", "optional"):
                for entry in MANIFEST[target].get(bucket, []):
                    assert entry["name"]
                    re.compile(entry["pattern"])  # raises if invalid

    def test_supabase_url_pattern_accepts_real_shape(self) -> None:
        spec = MANIFEST["vercel"]
        errors, _ = check_env.check_target("vercel", {"required": [
            e for e in spec["required"] if e["name"] == "SUPABASE_URL"
        ], "optional": []}, {"SUPABASE_URL": "https://abcdef123.supabase.co"})
        assert errors == []


# ═════════════════════════════════════════════════════════════════════════════
# Integration: the real .env satisfies store_pc (skips if .env absent)
# ═════════════════════════════════════════════════════════════════════════════

class TestRealDotenv:
    def test_real_env_passes_store_pc(self) -> None:
        env_path = ROOT / ".env"
        if not env_path.exists():
            pytest.skip(".env not present in this environment")
        env: dict = {}
        check_env.load_dotenv_into(env_path, env)
        errors, _ = check_env.check_target("store_pc", MANIFEST["store_pc"], env)
        assert errors == [], f"real .env fails store_pc: {errors}"
