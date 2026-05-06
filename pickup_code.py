"""
Pickup-code generator (block 4 of plan v2).

A pickup code is a short, human-readable identifier the customer shows at
the counter to claim their printed job. It must be:

- Easy to read aloud over the phone or at a busy counter.
- Hard to guess (a customer should not be able to look up someone else's
  job by trying nearby codes).
- Globally unique across all live jobs (collision = wrong customer's job
  handed over).

Format: ``P-XXXX`` (6 characters total, including the literal ``P-`` prefix
and 4 random characters drawn from a 30-character ambiguity-free alphabet).

  - Alphabet excludes: 0, 1, I, L, O, Q (look-alikes / hard to read).
  - 30^4 = 810,000 combinations. With expected job volume of <100/day and
    codes recycled after delivery, the collision-check loop in
    ``claim_unique_pickup_code`` virtually never executes more than once.

The generator uses ``secrets.choice`` so codes cannot be predicted from
prior codes (defence against the adjacent-code-guessing attack).
"""
from __future__ import annotations

import secrets
from typing import Protocol

PICKUP_CODE_PREFIX = "P-"
PICKUP_CODE_BODY_LENGTH = 4
PICKUP_CODE_LENGTH = len(PICKUP_CODE_PREFIX) + PICKUP_CODE_BODY_LENGTH

# Ambiguity-free alphabet: no 0/1/I/L/O/Q.
# 22 letters + 8 digits = 30 symbols. 30 ** 4 = 810,000.
PICKUP_CODE_ALPHABET = "ABCDEFGHJKMNPRSTUVWXYZ23456789"

# Hard cap on the collision retry loop. With 810k-space and sparse usage,
# 25 iterations means ~5e-3 % chance of failing for a fully-loaded space.
# In practice the loop exits on the first attempt almost every time.
_MAX_COLLISION_RETRIES = 25


class _SupabaseLike(Protocol):
    """Minimal protocol of the Supabase client subset we actually use.

    Defined here so the module is testable without importing the real
    supabase SDK in tests.
    """

    def table(self, name: str): ...  # noqa: D401, ANN201


def generate_pickup_code() -> str:
    """Return a fresh, cryptographically random pickup code.

    Does not check uniqueness. For uniqueness use ``claim_unique_pickup_code``.
    """
    body = "".join(
        secrets.choice(PICKUP_CODE_ALPHABET)
        for _ in range(PICKUP_CODE_BODY_LENGTH)
    )
    return f"{PICKUP_CODE_PREFIX}{body}"


def is_valid_pickup_code(code: str) -> bool:
    """Cheap structural validation. Used by the public tracker page to
    reject obviously-malformed input before hitting the DB."""
    if not isinstance(code, str):
        return False
    if len(code) != PICKUP_CODE_LENGTH:
        return False
    if not code.startswith(PICKUP_CODE_PREFIX):
        return False
    body = code[len(PICKUP_CODE_PREFIX):]
    return all(ch in PICKUP_CODE_ALPHABET for ch in body)


def claim_unique_pickup_code(client: _SupabaseLike) -> str:
    """Generate a pickup code that does not collide with any existing
    ``jobs.pickup_code`` value.

    Raises ``RuntimeError`` if a unique code cannot be found within
    ``_MAX_COLLISION_RETRIES`` attempts (effectively impossible under
    realistic load; treat as a sign the code space needs widening).
    """
    for _ in range(_MAX_COLLISION_RETRIES):
        candidate = generate_pickup_code()
        result = (
            client.table("jobs")
            .select("job_id")
            .eq("pickup_code", candidate)
            .limit(1)
            .execute()
        )
        if not getattr(result, "data", None):
            return candidate
    raise RuntimeError(
        f"failed to find a unique pickup code after {_MAX_COLLISION_RETRIES} attempts"
    )
