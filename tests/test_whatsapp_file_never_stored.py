"""A file we never received must not become a job we charge for.

On 2026-09-05 a customer sent a PDF over WhatsApp, was quoted ₹3, paid it, and
was given pickup code P-KK46 for a file Supabase Storage never received. The
job row carried `file_url = ""`. Nothing raised, nothing alerted, and every
console showed a normal paid job.

Three things had to line up, and all three were in this path:

1. `upload_file()` returned `""` on failure — an empty string where a URL
   belongs, so a caller could not tell a lost file from a stored one. The same
   shape as the ₹0 rate-card bugs: failing to the cheapest thing rather than
   failing loud.
2. The webhook wrote that value into `jobs.file_url` without checking it.
3. The receipt and the first quote question are sent in phase one, *before* the
   file is fetched — deliberately, so the customer gets an instant reply — so
   the conversation carried on to a quote and a payment link regardless.

The fix keeps the fast receipt and stops the flow when the file does not
arrive: `upload_file` returns None, the webhook abandons the job rather than
recording a bogus URL, the customer is asked to resend, and ops_watchdog is
told. The job row is left in place with an empty `file_url` — honest, and
unpullable — because a deleted job is one nobody can explain later.
"""

import ast
import sys
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_STUBS = [
    "gspread", "google", "google.auth", "google.auth.transport",
    "google.auth.transport.requests", "google.oauth2", "google.oauth2.service_account",
    "websockets", "requests", "pysnmp", "pysnmp.hlapi",
    "watchdog", "watchdog.observers", "watchdog.events", "razorpay", "dotenv",
]
for _mod in _STUBS:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None  # type: ignore

import pytest

import db_cloud


# ── upload_file cannot report success it did not have ────────────────────────

def test_a_failed_upload_returns_none_and_alerts():
    reports = []
    with patch("db_cloud._client") as mc, \
         patch("db_cloud._report", lambda *a: reports.append(a)):
        mc.return_value.storage.from_.return_value.upload.side_effect = RuntimeError("boom")
        out = db_cloud.upload_file("notes.pdf", b"x" * 10, "application/pdf")

    assert out is None, (
        f"upload_file returned {out!r}. An empty string is what a caller writes "
        "into jobs.file_url without noticing — that is the whole bug."
    )
    assert reports and reports[0][1] is False, "a lost customer file must alert"


def test_an_upload_that_yields_no_url_is_a_failure():
    """Storage accepting the bytes but returning no public URL is not success:
    no store PC can fetch a file it has no address for."""
    reports = []
    with patch("db_cloud._client") as mc, \
         patch("db_cloud._report", lambda *a: reports.append(a)):
        mc.return_value.storage.from_.return_value.get_public_url.return_value = ""
        out = db_cloud.upload_file("notes.pdf", b"x", "application/pdf")

    assert out is None
    assert reports and reports[0][1] is False


def test_a_real_upload_returns_the_url_and_clears_the_check():
    reports = []
    with patch("db_cloud._client") as mc, \
         patch("db_cloud._report", lambda *a: reports.append(a)):
        mc.return_value.storage.from_.return_value.get_public_url.return_value = "https://x/y.pdf"
        out = db_cloud.upload_file("notes.pdf", b"x", "application/pdf")

    assert out == "https://x/y.pdf"
    assert reports and reports[0][1] is True, (
        "a check that only ever reports failure latches red forever — "
        "recovery has to be announced too"
    )


def test_upload_file_never_returns_an_empty_string_again():
    """Read the source, because this is the specific regression to prevent and
    a mock cannot prove the absence of a return path."""
    tree = ast.parse((ROOT / "db_cloud.py").read_text(encoding="utf-8-sig"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "upload_file")
    for node in ast.walk(fn):
        if (isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)
                and node.value.value == ""):
            pytest.fail(
                "upload_file returns \"\" again — a caller cannot tell that "
                "from a URL, and jobs.file_url gets written empty"
            )


# ── the webhook stops rather than quoting for a file it does not have ────────

def _fn(name: str) -> ast.FunctionDef:
    tree = ast.parse((ROOT / "api" / "index.py").read_text(encoding="utf-8-sig"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def test_the_webhook_checks_the_upload_before_recording_it():
    """`insert_job_from_webhook(...)` must not be reached with a falsy url.

    `_handle_media` is the WhatsApp attachment path: it creates the job with an
    empty `file_url` on purpose for the fast receipt, then fetches. Recording
    whatever `upload_file` returned, unchecked, is what wrote `""` into the row.
    """
    src = ast.unparse(_fn("_handle_media"))
    assert "if not file_url" in src, (
        "the upload result is written to jobs.file_url unchecked again"
    )
    assert "_abandon_unfetched_job" in src, (
        "a failed fetch no longer stops the job being quoted for"
    )


def test_abandoning_a_job_tells_the_customer_in_plain_text():
    """`whatsapp_notify._send(phone, message)` takes TEXT and builds the Meta
    payload itself. Handing it a dict puts the dict in the message body."""
    fn = _fn("_abandon_unfetched_job")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_wa_send"]
    assert calls, "the customer is never told their file was lost"
    assert not isinstance(calls[0].args[1], ast.Dict), (
        "_send takes plain text, not a Meta payload dict"
    )


def test_abandoning_a_job_clears_the_session_and_alerts():
    """Leaving the session at step=size lets the bot quote and take payment for
    a file that is not there — which is the harm being fixed."""
    src = ast.unparse(_fn("_abandon_unfetched_job"))
    assert "clear_session" in src, "the bot would carry on to a quote"
    assert "whatsapp.file_never_stored" in src, "nobody is told a file was lost"


def test_abandoning_a_job_does_not_delete_it():
    """Cancel, never delete — a deleted job is one nobody can explain later.
    The row stays with an empty file_url, which is honest and unpullable."""
    src = ast.unparse(_fn("_abandon_unfetched_job")).lower()
    assert ".delete(" not in src and "delete_job" not in src
