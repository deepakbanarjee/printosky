"""
Cron workflows must not swallow a failure.

Through the 2026-10-17 egress outage every one of these endpoints answered 500
on every run — /cron/sla-check and /cron/abandoned-carts for fourteen days
straight — and every GitHub Actions job stayed green, because each step ended
in `exit 0` after printing the status it had just captured. Nobody was told,
and the red run in the Actions tab is the one alert channel here that needs
neither the database nor WhatsApp, both of which were down that fortnight.

This is the ratchet that keeps that fixed, in the same spirit as
test_fail_loud_rule.py: the pattern may not come back.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent
                    / ".github" / "workflows").glob("*cron*.yml"))


def _run_steps(path: Path) -> list[tuple[str, str]]:
    """Every (step name, run script) in a workflow, in order."""
    spec = yaml.safe_load(path.read_text())
    return [(st.get("name", "?"), st["run"])
            for job in spec["jobs"].values()
            for st in job["steps"] if "run" in st]


def _probe_steps(path: Path) -> list[tuple[str, str]]:
    """Only the steps that capture an HTTP status from a cron endpoint."""
    return [(n, r) for n, r in _run_steps(path) if "http_code" in r]


def test_there_are_cron_workflows_to_check():
    """Guard the guard: a bad glob would make every test below vacuous."""
    assert len(WORKFLOWS) >= 7, [p.name for p in WORKFLOWS]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_cron_step_ends_by_discarding_its_status(path):
    """`exit 0` at the end of a probe step is the bug, by name."""
    for name, run in _run_steps(path):
        assert not re.search(r"^\s*exit 0\s*$", run, re.M), (
            f"{path.name} / {name!r} ends in `exit 0`, which throws away the "
            f"status it just captured — the job cannot ever go red"
        )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_probe_step_fails_the_job_on_a_non_200(path):
    for name, run in _probe_steps(path):
        assert re.search(r"^\s*exit 1\s*$", run, re.M), (
            f"{path.name} / {name!r} reads $code but never exits non-zero, "
            f"so a 500 from the endpoint leaves the run green"
        )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_the_guard_really_exits_nonzero(path):
    """Execute the step's own script with curl stubbed. Shape is not behaviour.

    000 is in here deliberately: that is what curl reports when it cannot
    reach the host at all, which is a worse outage than a 500 and must also
    fail the job.
    """
    if shutil.which("bash") is None:          # pragma: no cover - CI has bash
        pytest.skip("bash not available")

    for name, run in _probe_steps(path):
        script = re.sub(r'code=\$\(curl.*?\)\n', 'code="$FAKE_CODE"\n',
                        run, flags=re.S)
        assert 'code="$FAKE_CODE"' in script, f"could not stub curl in {name!r}"

        for code, expected_ok in (("200", True), ("500", False),
                                  ("503", False), ("000", False)):
            done = subprocess.run(["bash", "-c", script],
                                  capture_output=True, text=True,
                                  env={"PATH": "/usr/bin:/bin", "FAKE_CODE": code})
            if expected_ok:
                assert done.returncode == 0, (
                    f"{path.name} / {name!r} failed on a healthy 200: "
                    f"{done.stdout}{done.stderr}")
            else:
                assert done.returncode != 0, (
                    f"{path.name} / {name!r} stayed green on HTTP {code}")
                assert "::error::" in done.stdout, (
                    f"{path.name} / {name!r} failed on HTTP {code} without "
                    f"annotating why")
