"""
B-7 — what a store can finish in-house, and what has to leave it.

One rule runs through all of it, and it is the safe direction:

    **Absent or false means outsourced.**

A new store claims nothing until someone writes the claim down, because the
claim is what decides whether a customer is promised the job today or next week.
Getting that backwards would have a store confidently accept binding it cannot
do (plan §4.7, decision B9).

Nothing calls `is_outsourced` yet — it is wired in B-8, along with the transfer
and the revenue split. Until then `FINISHING_OUTSOURCED` still answers, which is
why the no-context tests below matter most: they are the ones proving today's
behaviour is untouched.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import rate_card
import store_config
from rate_card import FINISHING_CAPABILITY, FINISHING_OUTSOURCED, is_outsourced
from store_config import KNOWN_CAPABILITIES, _normalise_capabilities

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _store(name):
    path = os.path.join(ROOT, "config", "stores", f"{name}.store_config.json")
    return json.load(open(path, encoding="utf-8"))


# ── Parsing a store's claim ───────────────────────────────────────────────────

def test_absent_capabilities_claim_nothing():
    assert _normalise_capabilities(None) == {}
    assert _normalise_capabilities({}) == {}


def test_a_non_dict_cannot_crash_a_store_pc():
    """This is read at import time on every box. A bad value must not raise."""
    for junk in ("binding", 42, ["binding"], True):
        assert _normalise_capabilities(junk) == {}


def test_only_known_names_survive():
    """A typo cannot invent a capability the code will never ask about."""
    caps = _normalise_capabilities({"binding": True, "bindng": True, "laminate": True})
    assert caps == {"binding": True}


def test_names_are_case_and_space_insensitive():
    assert _normalise_capabilities({" ROLL_LAM ": True}) == {"roll_lam": True}


def test_values_are_coerced_not_trusted():
    caps = _normalise_capabilities({"binding": "yes", "foiling": 0, "roll_lam": None})
    assert caps == {"binding": True, "foiling": False, "roll_lam": False}


# ── The stores as configured ──────────────────────────────────────────────────

def test_nattika_is_the_finishing_shop():
    assert _store("PRINTK")["capabilities"] == {
        "binding": True, "foiling": True, "roll_lam": True}


@pytest.mark.parametrize("name", ["OSP", "PRIOFF"])
def test_every_other_store_claims_nothing(name):
    """Not an oversight — the default is what keeps a store honest."""
    assert "capabilities" not in _store(name)


def test_every_store_config_still_parses():
    for name in ("OSP", "PRINTK", "PRIOFF"):
        caps = _normalise_capabilities(_store(name).get("capabilities"))
        assert set(caps) <= set(KNOWN_CAPABILITIES)


# ── is_outsourced ─────────────────────────────────────────────────────────────

def test_without_store_context_the_answer_is_the_safe_default():
    """No context means outsourced for anything on the list, in-house for the
    rest — the fallback every caller sees until it passes a store."""
    for finishing in FINISHING_OUTSOURCED:
        assert is_outsourced(finishing) is True
    for finishing in ("none", "staple", "spiral", "wiro", "perfect",
                      "lam_sheet", "id_card"):
        assert is_outsourced(finishing) is False


def test_soft_binding_is_nattikas_not_oxygens():
    """Owner, 2026-09-01: in-house at Nattika, outsourced at Oxygen.

    This is the case the capability map exists for. Before it, soft was in
    neither list — so the consoles said outsourced and the rate card said
    in-house, and both were half right about a question with two answers.
    """
    assert is_outsourced("soft", capabilities={}) is True             # Oxygen
    assert is_outsourced("soft", capabilities={"binding": True}) is False   # Nattika
    assert is_outsourced("soft") is True    # no context -> the safe default


def test_a_store_that_owns_the_machine_keeps_the_work():
    caps = {"binding": True, "foiling": True, "roll_lam": True}
    for finishing in FINISHING_OUTSOURCED:
        assert is_outsourced(finishing, capabilities=caps) is False


def test_claiming_nothing_outsources_everything():
    for finishing in FINISHING_OUTSOURCED:
        assert is_outsourced(finishing, capabilities={}) is True
        assert is_outsourced(finishing, capabilities={"binding": False}) is True


def test_one_capability_does_not_grant_another():
    binder = {"binding": True}
    assert is_outsourced("project", capabilities=binder) is False
    assert is_outsourced("lam_roll", capabilities=binder) is True


def test_an_in_house_finishing_is_never_outsourced_by_a_missing_capability():
    """Spiral has always been done in-house everywhere. Capability-gating it
    would let a store lose work it has always done by not listing a claim."""
    for caps in ({}, {"binding": False}, {"binding": True}):
        assert is_outsourced("spiral", capabilities=caps) is False
        assert is_outsourced("staple", capabilities=caps) is False


def test_the_finishing_is_matched_loosely_but_the_answer_is_not():
    assert is_outsourced("  LAM_ROLL ") is True
    assert is_outsourced("") is False
    assert is_outsourced(None) is False


def test_every_outsourced_finishing_has_a_capability_that_could_own_it():
    """Otherwise a store could buy the machine and still be told to send it out."""
    for finishing in FINISHING_OUTSOURCED:
        assert finishing in FINISHING_CAPABILITY, \
            f"{finishing} can never be brought in-house by any capability"
        assert FINISHING_CAPABILITY[finishing] in KNOWN_CAPABILITIES


def test_no_in_house_finishing_is_capability_gated():
    """The map must not grow to cover work that is never outsourced."""
    assert set(FINISHING_CAPABILITY) == set(FINISHING_OUTSOURCED)


# ── Asking about somebody else's store ────────────────────────────────────────

def test_another_stores_question_falls_back_rather_than_guessing(monkeypatch):
    """A box must never answer for a store whose machines it cannot see."""
    class Cfg:
        store_id = "OSP"
        capabilities = {"binding": True}
    monkeypatch.setattr(store_config, "get_store_config", lambda: Cfg())
    assert rate_card._active_store_capabilities("PRINTK") is None
    assert is_outsourced("project", store_id="PRINTK") is True
    # ...and its own store it can answer for.
    assert rate_card._active_store_capabilities("osp") == {"binding": True}


def test_a_missing_config_is_not_an_answer(monkeypatch):
    def boom():
        raise RuntimeError("no config on this box")
    monkeypatch.setattr(store_config, "get_store_config", boom)
    assert rate_card._active_store_capabilities(None) is None
    assert is_outsourced("project") is True


# ── The known divergence, recorded rather than silently resolved ──────────────

def test_the_consoles_and_the_rate_card_now_agree():
    """Settled 2026-09-01. This test replaces the one that pinned the
    divergence: both sides now list the same six finishings as outsourced by
    default, and the per-store answer comes from capabilities rather than from
    whichever file you happened to read.
    """
    js = open(os.path.join(ROOT, "website", "jobs.html"), encoding="utf-8").read()
    line = [l for l in js.splitlines() if "const OUTSOURCED_FINISHING" in l][0]
    console = set(x.strip(' "\'') for x in
                  line.split("[")[1].split("]")[0].split(","))
    assert console == set(FINISHING_OUTSOURCED)


def test_soft_is_not_claimed_in_house_by_the_other_list_as_well():
    """FINISHING_INHOUSE and FINISHING_OUTSOURCED must not both claim it."""
    from rate_card import FINISHING_INHOUSE
    assert not set(FINISHING_INHOUSE) & set(FINISHING_OUTSOURCED)
    assert "soft" not in FINISHING_INHOUSE
