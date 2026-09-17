"""Printosky domain core — the rules, with no I/O.

Everything in this package is pure Python on the standard library only. No
Supabase, no HTTP, no SQLite, no printer. That constraint is the point: the
same rules have to hold on Vercel, on a store PC that is offline, and in a
unit test, and the only way to be sure of that is to make the rules unable to
reach any of those places.

The layering the rest of the repo should follow:

    core/          rules        pure, importable anywhere, no I/O
    api/v2/        transport    HTTP in, JSON out, calls core
    api/v2/repos   adapters     Supabase/in-memory implementations of core.ports
    website/app/   presentation ES modules against the v2 JSON envelope

Legacy code (``api/index.py``'s if-chain, ``db_cloud.py``, ``print_server.py``)
keeps working untouched; it may import from here, and the P0 fixes do, but
nothing here imports from it.

Design references:
  docs/V2_ARCHITECTURE.md                      — the upgrade this belongs to
  docs/reviews/2026-09-10-professional-review.md §7 — the data contracts
"""

__all__ = [
    "errors",
    "identity",
    "money",
    "orders",
    "payments",
    "ports",
    "pricing",
]

# Schema/behaviour version of the core contracts. Bump on a breaking change to
# any dataclass field or state-machine edge; /v2/health reports it so a store
# PC and the cloud can notice they disagree instead of failing mysteriously.
CORE_CONTRACT_VERSION = "2.0.0"
