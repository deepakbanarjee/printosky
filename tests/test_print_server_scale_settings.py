"""
The SumatraPDF command line send_to_printer builds — with and without scaling.

`noscale` is a guard, not the mechanism: the geometry is already baked into the
file by print_planner, and this token only stops a driver having a second
opinion. So the one thing that matters here is that a job which did NOT ask for
scaling produces the exact same command it always did.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import print_server


@pytest.fixture
def sent(monkeypatch, tmp_path):
    """Capture the argv send_to_printer would have run."""
    calls = []

    class Result:
        returncode = 0
        stdout = stderr = ""

    monkeypatch.setattr(print_server, "find_sumatra", lambda: r"C:\SumatraPDF.exe")
    monkeypatch.setattr(print_server, "update_job_status", lambda *a, **k: None)
    monkeypatch.setattr(print_server, "_trigger_printer_poll_now", lambda *a, **k: None)
    monkeypatch.setattr(print_server, "PRINTERS", {"konica": "KONICA-Q", "epson": "EPSON-Q"})
    monkeypatch.setattr(print_server, "PRINTER_IPS", {"konica": "10.0.0.1"})
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), Result())[1])

    pdf = tmp_path / "job.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF")

    def _send(**kwargs):
        calls.clear()
        ok, _ = print_server.send_to_printer(
            "OSP-1", str(pdf), "konica", update_status=False, **kwargs)
        assert ok
        argv = calls[0]
        return argv[argv.index("-print-settings") + 1]

    return _send


class TestWithoutScaling:
    """Every job in production today. The command must not have changed."""

    def test_plain_job_is_exactly_as_before(self, sent):
        assert sent(copies=1, colour_mode="bw", sides="ss") == "1x,monochrome,simplex"

    def test_duplex_colour_a4(self, sent):
        assert sent(copies=2, colour_mode="colour", sides="ds",
                    paper_size="A4") == "2x,color,duplexlong,paper=A4"

    def test_noscale_is_never_emitted_by_default(self, sent):
        for kwargs in (
            {"copies": 1, "colour_mode": "bw"},
            {"copies": 1, "colour_mode": "auto", "sides": "ds", "paper_size": "A3"},
            {"copies": 3, "colour_mode": "colour", "orientation": "landscape"},
        ):
            assert "noscale" not in sent(**kwargs)

    def test_explicit_false_matches_the_default(self, sent):
        assert (sent(copies=1, colour_mode="bw", sides="ds")
                == sent(copies=1, colour_mode="bw", sides="ds", scale_applied=False))


class TestWithScaling:

    def test_noscale_is_appended(self, sent):
        assert sent(copies=1, colour_mode="bw", sides="ss",
                    scale_applied=True) == "1x,monochrome,simplex,noscale"

    def test_it_only_adds_that_one_token(self, sent):
        without = sent(copies=2, colour_mode="colour", sides="ds", paper_size="A4")
        with_ = sent(copies=2, colour_mode="colour", sides="ds", paper_size="A4",
                     scale_applied=True)
        assert with_ == f"{without},noscale"

    def test_it_comes_last_so_nothing_else_shifts(self, sent):
        parts = sent(copies=1, colour_mode="bw", sides="ds", paper_size="A4",
                     orientation="portrait", scale_applied=True).split(",")
        assert parts[-1] == "noscale"
        assert parts[0] == "1x"
