"""
pin_crypto.py — the one implementation of staff PIN hashing.

Why this module exists
----------------------
A staff PIN is created on one machine and verified on another. The CLI
(`staff_setup.py`) seeds it on the store PC, `print_server.py` checks it at the
counter, and `api/index.py` checks it in the cloud. All three must derive the
same bytes from the same PIN or a member of staff is silently locked out of
whichever surface drifted.

Until now each of those three files carried its own copy of the PBKDF2 code and
its own literal `260_000`. Nothing tied them together, so the iteration count
was three independent constants that merely happened to agree — and the cost of
them disagreeing is paid at the counter, at the till, by someone who cannot log
in and cannot see why.

Storage format
--------------
`staff.pin_hash` + `staff.pin_salt` (schema v15). A NULL salt marks a legacy
row hashed with bare SHA-256 before v15; `verify_pin` still accepts those so
old rows keep working, and any re-hash through `hash_pin` upgrades them to
PBKDF2. Do not add a new caller that writes a NULL salt.
"""

import hashlib
import hmac
import secrets

# Raising this invalidates nothing — existing hashes keep their own cost baked
# into the stored digest only if you also store the count, which we do NOT. So
# a change here makes every existing PIN unverifiable everywhere. Treat it as
# frozen unless you are running a migration that re-hashes every staff row.
PBKDF2_ITERATIONS = 260_000

_SALT_BYTES = 16


def hash_pin(pin: str) -> tuple[str, str]:
    """Hash a PIN for storage. Returns (hash_hex, salt_hex), both new."""
    salt = secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", pin.encode(), salt.encode(), PBKDF2_ITERATIONS
    ).hex()
    return digest, salt


def verify_pin(pin: str, stored_hash: str, stored_salt: str | None) -> bool:
    """Constant-time PIN check against a stored hash.

    `stored_salt` of None selects the pre-v15 legacy SHA-256 path.
    """
    if stored_salt is None:
        return hmac.compare_digest(
            stored_hash, hashlib.sha256(pin.encode()).hexdigest()
        )
    expected = hashlib.pbkdf2_hmac(
        "sha256", pin.encode(), stored_salt.encode(), PBKDF2_ITERATIONS
    ).hex()
    return hmac.compare_digest(stored_hash, expected)


def sha256_hex(text: str) -> str:
    """Bare SHA-256. Admin-password comparison only — never for a new PIN."""
    return hashlib.sha256(text.encode()).hexdigest()
