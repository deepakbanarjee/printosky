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
        # Either branch@sha (named branch) or bare sha (detached HEAD, e.g. in CI).
        sha_part = v.removesuffix("+dirty")
        if "@" in sha_part:
            sha = sha_part.split("@", 1)[1]
        else:
            sha = sha_part
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
            ("status", "--porcelain", "--untracked-files=no"): " M watcher.py",
        }.get(a))
        assert app_version._compute() == "main@a1b2c3d+dirty"

    def test_clean_tree_is_not_flagged(self, monkeypatch):
        monkeypatch.setattr(app_version, "_git", lambda *a: {
            ("rev-parse", "--short", "HEAD"): "a1b2c3d",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain", "--untracked-files=no"): "",
        }.get(a))
        assert app_version._compute() == "main@a1b2c3d"

    def test_detached_head_reports_bare_sha(self, monkeypatch):
        """`git rev-parse --abbrev-ref` returns the literal 'HEAD' when
        detached — reporting 'HEAD@a1b2c3d' would be noise."""
        monkeypatch.setattr(app_version, "_git", lambda *a: {
            ("rev-parse", "--short", "HEAD"): "a1b2c3d",
            ("rev-parse", "--abbrev-ref", "HEAD"): "HEAD",
            ("status", "--porcelain", "--untracked-files=no"): "",
        }.get(a))
        assert app_version._compute() == "a1b2c3d"


class TestDegradedEnvironments:
    def test_no_git_binary_falls_back_to_reading_dot_git(self, monkeypatch):
        """A store PC without git on PATH should still name its commit — and
        say that it cannot vouch for the tree, because .git/HEAD gives a sha
        and nothing at all about local edits."""
        monkeypatch.setattr(app_version, "_git", lambda *a: None)
        monkeypatch.setattr(app_version, "_read_head_fallback", lambda: "deadbee")
        assert app_version._compute() == "deadbee+unknown"

    def test_unknown_when_nothing_can_be_read(self, monkeypatch):
        """Never invent a version — an unreadable checkout must say so."""
        monkeypatch.setattr(app_version, "_git", lambda *a: None)
        monkeypatch.setattr(app_version, "_read_head_fallback", lambda: None)
        assert app_version._compute() == app_version.UNKNOWN

    def test_undeterminable_dirtiness_is_not_reported_as_clean(self, monkeypatch):
        """_git returns None on failure and "" on a clean tree. Conflating the
        two labels an unknown state as clean.

        This test used to assert `main@a1b2c3d` — which IS the clean rendering,
        so it accepted exactly the conflation its name and docstring warn
        about. A guard that passes on the bug it describes is worse than no
        guard: it makes the next person believe the case is covered.
        """
        monkeypatch.setattr(app_version, "_git", lambda *a: {
            ("rev-parse", "--short", "HEAD"): "a1b2c3d",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain", "--untracked-files=no"): None,   # could not tell
        }.get(a))
        assert app_version._compute() == "main@a1b2c3d+unknown", \
            "an undeterminable tree state must not be rendered as a clean one"

    def test_git_failure_never_raises(self, monkeypatch):
        """A version lookup must not be able to take down the agent that
        reports it."""
        def boom(*a, **k):
            raise OSError("git exploded")
        monkeypatch.setattr(app_version.subprocess, "run", boom)
        assert app_version._git("rev-parse", "HEAD") is None


class TestGeneratedFilesDoNotFakeDirtiness:
    """A store PC generates files at setup time. If one of those is not
    gitignored it shows as untracked, `+dirty` fires on every correctly
    configured box, and the flag stops meaning "someone hand-patched this".

    That is exactly what happened on first rollout: SETUP_AUTOSTART.bat writes
    boot_delay.vbs, which was untracked, so Nattika reported
    `main@db14849+dirty` on a tree whose `git diff` was empty.
    """

    def test_setup_generated_files_are_gitignored(self):
        import subprocess

        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        # Files written by the setup/boot scripts onto each store PC.
        generated = ["boot_delay.vbs"]

        not_ignored = []
        for name in generated:
            r = subprocess.run(["git", "check-ignore", "-q", name],
                               cwd=root, capture_output=True)
            if r.returncode != 0:
                not_ignored.append(name)

        assert not not_ignored, (
            f"{not_ignored} is generated on every store PC but is not gitignored, "
            "so app_version will report every properly-configured box as +dirty "
            "and the flag becomes meaningless. Add it to .gitignore."
        )


class TestUntrackedFilesDoNotFakeDirtiness:
    """`+dirty` means someone hand-patched this box's code. `git status
    --porcelain` also lists UNTRACKED files, so without --untracked-files=no
    one stray download says the same thing — and `tools/scale_proof.py` writes
    ./scale_proof into the repo by default, so running the Phase 2 proof marks
    that box dirty for good.

    TestGeneratedFilesDoNotFakeDirtiness gitignores the generated files we know
    about. This closes the case for the ones we do not.
    """

    def test_untracked_files_are_not_counted(self, monkeypatch):
        seen = []

        def fake_git(*a):
            seen.append(a)
            return {("rev-parse", "--short", "HEAD"): "a1b2c3d",
                    ("rev-parse", "--abbrev-ref", "HEAD"): "main",
                    ("status", "--porcelain", "--untracked-files=no"): ""}.get(a)

        monkeypatch.setattr(app_version, "_git", fake_git)
        assert app_version._compute() == "main@a1b2c3d"
        assert ("status", "--porcelain", "--untracked-files=no") in seen, (
            "status was asked without --untracked-files=no, so an untracked "
            "file will report this box as hand-patched")

    def test_a_real_edit_to_a_tracked_file_still_shows(self, monkeypatch):
        """The flag has to keep working for the case it exists for."""
        monkeypatch.setattr(app_version, "_git", lambda *a: {
            ("rev-parse", "--short", "HEAD"): "a1b2c3d",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain", "--untracked-files=no"): " M print_server.py",
        }.get(a))
        assert app_version._compute() == "main@a1b2c3d+dirty"

    def test_the_three_states_are_all_distinguishable(self, monkeypatch):
        """clean, hand-patched, and unreadable must not render alike."""
        def at(status):
            monkeypatch.setattr(app_version, "_git", lambda *a: {
                ("rev-parse", "--short", "HEAD"): "a1b2c3d",
                ("rev-parse", "--abbrev-ref", "HEAD"): "main",
                ("status", "--porcelain", "--untracked-files=no"): status,
            }.get(a))
            return app_version._compute()

        assert len({at(""), at(" M watcher.py"), at(None)}) == 3
