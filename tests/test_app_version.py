"""Tests for app_version — naming the commit a store PC is actually running.

This is what makes `store_devices.app_version` meaningful, so the failure that
matters is a *wrong* answer, not a missing one: reporting a clean tree when it
is dirty, or a plausible-looking version when git could not be read at all,
would send someone debugging the wrong code on the wrong box.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import app_version  # noqa: E402


class TestVersionString:
    def test_reports_branch_and_sha_for_this_checkout(self):
        v = app_version.get_version()
        assert v and v != app_version.UNKNOWN, "could not read a version from a git checkout"
        # branch@sha, with the sha being a short hex hash.
        assert "@" in v, f"expected branch@sha, got {v!r}"
        sha = v.split("@", 1)[1].removesuffix("+dirty")
        assert 7 <= len(sha) <= 12, f"unexpected sha length in {v!r}"
        int(sha, 16)   # raises if it is not hex

    def test_get_version_matches_the_captured_constant(self):
        """The value is captured once at import — see the module docstring on
        why that is the honest answer for a running process."""
        assert app_version.get_version() == app_version.VERSION


class TestDirtyFlag:
    def test_dirty_tree_is_flagged(self, monkeypatch):
        monkeypatch.setattr(app_version, "_git", lambda *a: {
            ("rev-parse", "--short", "HEAD"): "a1b2c3d",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain"): " M watcher.py",
        }.get(a))
        assert app_version._compute() == "main@a1b2c3d+dirty"

    def test_clean_tree_is_not_flagged(self, monkeypatch):
        monkeypatch.setattr(app_version, "_git", lambda *a: {
            ("rev-parse", "--short", "HEAD"): "a1b2c3d",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain"): "",
        }.get(a))
        assert app_version._compute() == "main@a1b2c3d"

    def test_detached_head_reports_bare_sha(self, monkeypatch):
        """`git rev-parse --abbrev-ref` returns the literal 'HEAD' when
        detached — reporting 'HEAD@a1b2c3d' would be noise."""
        monkeypatch.setattr(app_version, "_git", lambda *a: {
            ("rev-parse", "--short", "HEAD"): "a1b2c3d",
            ("rev-parse", "--abbrev-ref", "HEAD"): "HEAD",
            ("status", "--porcelain"): "",
        }.get(a))
        assert app_version._compute() == "a1b2c3d"


class TestDegradedEnvironments:
    def test_no_git_binary_falls_back_to_reading_dot_git(self, monkeypatch):
        """A store PC without git on PATH should still name its commit."""
        monkeypatch.setattr(app_version, "_git", lambda *a: None)
        monkeypatch.setattr(app_version, "_read_head_fallback", lambda: "deadbee")
        assert app_version._compute() == "deadbee"

    def test_unknown_when_nothing_can_be_read(self, monkeypatch):
        """Never invent a version — an unreadable checkout must say so."""
        monkeypatch.setattr(app_version, "_git", lambda *a: None)
        monkeypatch.setattr(app_version, "_read_head_fallback", lambda: None)
        assert app_version._compute() == app_version.UNKNOWN

    def test_undeterminable_dirtiness_is_not_reported_as_clean(self, monkeypatch):
        """_git returns None on failure and "" on a clean tree. Conflating the
        two would silently label an unknown state as clean."""
        monkeypatch.setattr(app_version, "_git", lambda *a: {
            ("rev-parse", "--short", "HEAD"): "a1b2c3d",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain"): None,      # could not tell
        }.get(a))
        assert app_version._compute() == "main@a1b2c3d", \
            "an undeterminable tree state must not be flagged +dirty"

    def test_git_failure_never_raises(self, monkeypatch):
        """A version lookup must not be able to take down the agent that
        reports it."""
        def boom(*a, **k):
            raise OSError("git exploded")
        monkeypatch.setattr(app_version.subprocess, "run", boom)
        assert app_version._git("rev-parse", "HEAD") is None
