"""
No credential literals in tracked files.

On 2026-09-07 a Meta access token was committed to marketing/ and pushed to
this repository, which is public. It was a short-lived token in a
token-exchange script's fallback branch -- not the permanent one -- but a
credential in a tracked file is leaked the moment it is pushed, and rotating is
the only real remedy afterwards. GitHub's own push protection would have caught
it; this test catches it one step earlier, before the push exists.

Same shape as test_fail_loud_rule.py: a banned pattern that fails the build,
so the decision to add one has to be deliberate and visible in review.

Scope note: this scans TRACKED, text-like files only. .env is gitignored and is
where credentials belong -- the rule is not "no secrets on this machine", it is
"no secrets in git".
"""

import base64
import json
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Extensions worth scanning: source, config and docs. Binaries and rendered
# marketing assets are skipped -- base64 image payloads are full of accidental
# matches and carry no credentials.
SCANNED_SUFFIXES = {
    ".py", ".js", ".mjs", ".cjs", ".json", ".yml", ".yaml", ".toml",
    ".md", ".txt", ".sh", ".bat", ".ps1", ".env-example", ".example",
}

# Files that legitimately discuss the shape of a credential without being one.
ALLOWLIST = {
    ".env.example",
    "pdf-editor/frontend/.env.example",
    "tests/test_no_hardcoded_secrets.py",
}

PATTERNS = [
    # Meta / Facebook user, page and system-user tokens.
    ("Meta access token", re.compile(r"\bEAA[A-Za-z0-9]{60,}")),
    # Supabase / any JWT carrying a real payload. A Supabase *anon* key is one
    # of these and is meant to ship to the browser, so the role claim decides --
    # see _jwt_is_public below. A service_role key in a tracked file is the
    # serious case: it bypasses RLS entirely.
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{20,}")),
    # Anthropic keys.
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{40,}")),
    # Razorpay live secrets.
    ("Razorpay live key", re.compile(r"\brzp_live_[A-Za-z0-9]{10,}")),
]

# A data: URI is a base64 blob, not a credential, and "EAA"/"eyJ" turn up inside
# them by chance. Drop those lines before matching.
BASE64_NOISE = re.compile(r"base64,|data:image/|data:font/|data:application/")


# Roles a JWT may carry and still belong in a tracked file. `anon` is the
# browser-side Supabase key: publishing it is the design, and RLS is what
# actually protects the data behind it. Anything else -- service_role above all
# -- is a real credential and must not be committed.
PUBLIC_JWT_ROLES = {"anon"}


def _jwt_is_public(token: str) -> bool:
    """True when a JWT's role claim is one that is safe to publish.

    Undecodable payloads are treated as NOT public: a token we cannot read is
    exactly the one worth a human look.
    """
    parts = token.split(".")
    if len(parts) < 2:
        return False
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return False
    return claims.get("role") in PUBLIC_JWT_ROLES


def _tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    return [p for p in out.stdout.splitlines() if p]


def test_no_credential_literals_in_tracked_files() -> None:
    findings: list[str] = []
    for rel in _tracked_files():
        if rel in ALLOWLIST:
            continue
        path = ROOT / rel
        if path.suffix.lower() not in SCANNED_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if BASE64_NOISE.search(line):
                continue
            for label, pat in PATTERNS:
                m = pat.search(line)
                if not m:
                    continue
                if label == "JWT" and _jwt_is_public(m.group(0)):
                    continue
                # Report the location only -- never echo the secret, or the CI
                # log becomes the second place it leaks.
                findings.append(f"{rel}:{lineno}  ({label})")

    assert not findings, (
        "Credential literal(s) found in tracked files:\n  "
        + "\n  ".join(findings)
        + "\n\nMove the value into .env (gitignored) and read it with "
          "os.environ / a getpass prompt. If the value has ever been pushed, "
          "ROTATE it -- deleting the line does not un-leak it."
    )


@pytest.mark.parametrize("label,sample", [
    ("Meta access token", "tok = " + '"EAA' + "x" * 70 + '"'),
    ("Anthropic API key", "key = " + '"sk-ant-' + "y" * 45 + '"'),
    ("Razorpay live key", 'k = "rzp_live_' + "z" * 14 + '"'),
])
def test_patterns_actually_match_their_shape(label, sample) -> None:
    """A guard that matches nothing is worse than no guard."""
    pat = dict((l, p) for l, p in PATTERNS)[label]
    assert pat.search(sample), f"{label} pattern no longer matches its own shape"


def test_base64_image_payloads_do_not_trip_the_scan() -> None:
    """brand-kit HTML embeds images whose base64 contains 'EAA' by chance."""
    line = '<img src="data:image/png;base64,iVBORw0EAA' + "Q" * 80 + '">'
    assert BASE64_NOISE.search(line), "data: URIs must be excluded from the scan"


def test_anon_jwt_is_allowed_but_service_role_is_not() -> None:
    """The distinction the whole JWT rule turns on."""
    def _tok(role):
        head = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
        body = base64.urlsafe_b64encode(
            json.dumps({"role": role, "iss": "supabase"}).encode()).decode().rstrip("=")
        return f"{head}.{body}.{'s' * 30}"

    assert _jwt_is_public(_tok("anon")), "the browser-side key must stay allowed"
    assert not _jwt_is_public(_tok("service_role")), "a service key bypasses RLS"
    assert not _jwt_is_public("eyJnope.eyJalsonope.sig"), "undecodable is not public"
