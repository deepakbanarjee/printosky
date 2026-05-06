"""Unit tests for pickup_code (block 4 of plan v2)."""
from __future__ import annotations

import re
from collections import Counter

import pytest

from pickup_code import (
    PICKUP_CODE_ALPHABET,
    PICKUP_CODE_BODY_LENGTH,
    PICKUP_CODE_LENGTH,
    PICKUP_CODE_PREFIX,
    claim_unique_pickup_code,
    generate_pickup_code,
    is_valid_pickup_code,
)


# --- generate_pickup_code -------------------------------------------------


class TestGeneratePickupCode:
    def test_format_matches_spec(self):
        code = generate_pickup_code()
        assert len(code) == PICKUP_CODE_LENGTH
        assert code.startswith(PICKUP_CODE_PREFIX)
        assert re.fullmatch(rf"P-[{PICKUP_CODE_ALPHABET}]{{{PICKUP_CODE_BODY_LENGTH}}}", code)

    def test_alphabet_excludes_ambiguous_characters(self):
        for forbidden in "0", "1", "I", "L", "O", "Q":
            assert forbidden not in PICKUP_CODE_ALPHABET, f"{forbidden!r} must not be in alphabet"

    def test_codes_are_random_not_sequential(self):
        codes = [generate_pickup_code() for _ in range(50)]
        # at least 49 distinct (allow 1 birthday-paradox collision in 810k space)
        assert len(set(codes)) >= 49

    def test_alphabet_distribution_is_roughly_uniform(self):
        # 4000 chars across 30 symbols ~ 133 each. Accept [50, 250].
        bodies = "".join(generate_pickup_code()[2:] for _ in range(1000))
        counts = Counter(bodies)
        for ch in PICKUP_CODE_ALPHABET:
            assert 50 < counts[ch] < 250, f"char {ch!r}: count={counts[ch]} (skewed)"


# --- is_valid_pickup_code -------------------------------------------------


class TestIsValidPickupCode:
    @pytest.mark.parametrize("code", ["P-7K2N", "P-ABCD", "P-2345", "P-WXYZ"])
    def test_well_formed_codes_are_valid(self, code):
        assert is_valid_pickup_code(code)

    @pytest.mark.parametrize(
        "code",
        [
            "",
            "P",
            "P-",
            "P-ABC",        # too short
            "P-ABCDE",      # too long
            "X-ABCD",       # wrong prefix
            "P_ABCD",       # wrong separator
            "P-ABC0",       # contains 0 (ambiguous)
            "P-ABCI",       # contains I (ambiguous)
            "P-ABCQ",       # contains Q (ambiguous)
            "P-abcd",       # lowercase rejected
            None,           # not a string
            12345,          # not a string
            "PABCD",        # missing dash
        ],
    )
    def test_malformed_codes_are_rejected(self, code):
        assert not is_valid_pickup_code(code)

    def test_freshly_generated_codes_validate(self):
        for _ in range(20):
            assert is_valid_pickup_code(generate_pickup_code())


# --- claim_unique_pickup_code ---------------------------------------------


class _FakeQuery:
    def __init__(self, taken_codes):
        self._taken = taken_codes
        self._filter = None

    def select(self, *_a, **_kw):
        return self

    def eq(self, _column, value):
        self._filter = value
        return self

    def limit(self, _n):
        return self

    def execute(self):
        class R:
            pass
        r = R()
        r.data = [{"job_id": "OSP-EXISTING"}] if self._filter in self._taken else []
        return r


class _FakeClient:
    def __init__(self, taken_codes):
        self.taken = set(taken_codes)
        self.queries_made = 0

    def table(self, _name):
        self.queries_made += 1
        return _FakeQuery(self.taken)


class TestClaimUniquePickupCode:
    def test_returns_a_valid_code_when_space_is_empty(self):
        client = _FakeClient(taken_codes=set())
        code = claim_unique_pickup_code(client)
        assert is_valid_pickup_code(code)
        assert client.queries_made == 1  # found on first try

    def test_skips_collisions(self, monkeypatch):
        # Force the generator to return a known-taken code first, then a
        # known-free code, and confirm the second one is returned.
        scripted = iter(["P-XXXX", "P-FREE"])
        monkeypatch.setattr(
            "pickup_code.generate_pickup_code",
            lambda: next(scripted),
        )
        client = _FakeClient(taken_codes={"P-XXXX"})
        code = claim_unique_pickup_code(client)
        assert code == "P-FREE"
        assert client.queries_made == 2

    def test_raises_when_space_is_exhausted(self, monkeypatch):
        # Pretend every generated code is taken.
        monkeypatch.setattr("pickup_code.generate_pickup_code", lambda: "P-XXXX")
        client = _FakeClient(taken_codes={"P-XXXX"})
        with pytest.raises(RuntimeError, match="failed to find a unique pickup code"):
            claim_unique_pickup_code(client)
