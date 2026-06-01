#!/bin/bash
# SessionStart hook for Claude Code on the web.
# Creates a virtualenv and installs Python deps so the pytest suite runs the
# same way CI does (.github/workflows/test.yml). Idempotent + non-interactive.
#
# A venv is used because the base web image ships some distro-managed packages
# (pip, PyJWT, ...) that pip cannot uninstall/upgrade in place ("RECORD file
# not found"), which breaks a plain `pip install -r requirements.txt`.
set -euo pipefail

# Only run in the remote (web) environment; a local machine is already set up.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

VENV="$CLAUDE_PROJECT_DIR/.venv"

# Create the venv once; reuse it on resume/clear/compact.
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi

PY="$VENV/bin/python"

"$PY" -m pip install --upgrade pip

# Production / store-PC runtime deps.
"$PY" -m pip install -r requirements.txt

# Test-time + scripts-time deps not pinned in requirements.txt (see CI):
#  - pytest, pytest-cov : the test runner
#  - python-dotenv      : tests/conftest.py loads env via dotenv
#  - pyyaml             : scripts/check_schema.py manifest read/write
#  - pymupdf            : colour_detector.py uses `fitz` (store-PC only)
#  - reportlab          : invoice PDF generation path exercised by tests
"$PY" -m pip install pytest pytest-cov python-dotenv pyyaml pymupdf reportlab

# Put the venv on PATH for the rest of the session so `python`/`pytest` resolve
# to it, and export synthetic env vars so import-time os.environ[...] lookups
# don't KeyError during test collection (matches CI). Real secrets never enter
# this environment.
{
  echo "export VIRTUAL_ENV=$VENV"
  echo "export PATH=$VENV/bin:\$PATH"
  echo 'export RAZORPAY_KEY_ID=ci_key'
  echo 'export RAZORPAY_KEY_SECRET=ci_secret'
  echo 'export RAZORPAY_WEBHOOK_SECRET=ci_webhook_secret'
  echo 'export META_APP_SECRET=ci_meta_app_secret'
  echo 'export ADMIN_PASSWORD_HASH=ci_admin_password_hash'
  echo 'export UPTIME_NOTIFY_SECRET=ci_uptime_notify_secret'
} >> "$CLAUDE_ENV_FILE"
