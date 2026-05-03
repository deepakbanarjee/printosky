#!/bin/sh
# Install the local pre-commit syntax check hook.
#
# `.git/hooks/` is not tracked in git, so each clone has to set its hook up
# once. Run this from the repo root after cloning:
#
#     scripts/install_precommit_hook.sh
#
# After that, every `git commit` will refuse to land Python files that fail
# `py_compile`. See scripts/precommit_syntax_check.py for the actual logic.

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK_PATH="$REPO_ROOT/.git/hooks/pre-commit"

cat > "$HOOK_PATH" <<'HOOK'
#!/bin/sh
# Pre-commit syntax check.
# Source: scripts/precommit_syntax_check.py
# To regenerate from a fresh clone: scripts/install_precommit_hook.sh
exec python scripts/precommit_syntax_check.py
HOOK

chmod +x "$HOOK_PATH"
echo "Installed $HOOK_PATH"
