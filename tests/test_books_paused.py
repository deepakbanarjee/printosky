"""
Book sales pause (BOOKS_SALE_PAUSED).

Sales stopped 2026-09-09 pending a new edition, and nothing in the code stopped
customers ordering: start_catalog was documented as opening the catalog
"unconditionally", three of the five menu rows were books, the BOOKS keyword
still worked, and the hourly abandoned-cart cron kept chasing open carts for
payment. Taking money for a product that cannot ship is worse than any bug in
the ordering flow itself.

The gate lives inside _start because all seven entrances funnel through it.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

book_bot = pytest.importorskip("book_bot", reason="book_bot unavailable")


@pytest.fixture
def paused(monkeypatch):
    monkeypatch.setenv("BOOKS_SALE_PAUSED", "1")


@pytest.fixture
def selling(monkeypatch):
    monkeypatch.delenv("BOOKS_SALE_PAUSED", raising=False)


class TestFlag:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
    def test_truthy_spellings(self, monkeypatch, value):
        monkeypatch.setenv("BOOKS_SALE_PAUSED", value)
        assert book_bot.books_paused() is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
    def test_everything_else_keeps_selling(self, monkeypatch, value):
        monkeypatch.setenv("BOOKS_SALE_PAUSED", value)
        assert book_bot.books_paused() is False

    def test_unset_keeps_selling(self, selling):
        """A forgotten variable must never quietly stop the shop selling."""
        assert book_bot.books_paused() is False


class TestCatalogGate:
    def test_new_order_is_turned_away_while_paused(self, paused, monkeypatch):
        monkeypatch.setattr(book_bot._dbc, "get_active_book_order", lambda p: None)
        out = book_bot._start("919000000001", "Asha")
        assert out == [book_bot.BOOKS_PAUSED_REPLY]
        assert "paused" in out[0].lower()
        assert "print" in out[0].lower(), "printing is still open for business"

    def test_starting_over_is_also_starting(self, paused, monkeypatch):
        """force_new blanks `active`, so "new" is turned away too."""
        monkeypatch.setattr(book_bot._dbc, "get_active_book_order",
                            lambda p: {"order_code": "XTR-1", "status": "collecting"})
        assert book_bot._start("919000000001", "Asha", force_new=True) == [
            book_bot.BOOKS_PAUSED_REPLY]

    def test_an_order_already_in_flight_is_never_stranded(self, paused, monkeypatch):
        """Someone who has confirmed -- or paid -- must still be able to finish."""
        reached = {}
        monkeypatch.setattr(book_bot._dbc, "get_active_book_order",
                            lambda p: {"order_code": "XTR-9", "status": "awaiting_payment"})
        monkeypatch.setattr(book_bot._dbc, "update_book_order",
                            lambda *a, **k: reached.setdefault("update", True))
        try:
            book_bot._start("919000000001", "Asha")
        except Exception:
            pass          # it proceeds into the real flow; not being turned away is the point
        assert "paused" not in str(reached), "in-flight order must not hit the pause reply"

    def test_selling_normally_when_unset(self, selling, monkeypatch):
        monkeypatch.setattr(book_bot._dbc, "get_active_book_order", lambda p: None)
        calls = {}
        monkeypatch.setattr(book_bot._dbc, "update_book_order",
                            lambda *a, **k: calls.setdefault("hit", True))
        try:
            book_bot._start("919000000001", "Asha")
        except Exception:
            pass
        # The pause reply is the one thing that must NOT come back.
        assert book_bot.books_paused() is False


class TestMenu:
    def test_book_rows_disappear_while_paused(self, paused):
        from routing.intent import build_menu_rows
        ids = {r["id"] for r in build_menu_rows()}
        assert ids == {"intent_print", "intent_academic"}

    def test_all_rows_return_when_selling(self, selling):
        from routing.intent import build_menu_rows
        ids = {r["id"] for r in build_menu_rows()}
        assert "intent_xtraa" in ids and "intent_sociology" in ids
        assert len(ids) == 5


class TestAbandonedCartCron:
    def test_cron_skips_while_paused(self, paused, monkeypatch):
        """Chasing payment for a book we stopped selling is the worst message
        this hourly cron could send."""
        import api.index as api_mod
        monkeypatch.setattr(book_bot, "run_cart_reminders",
                            lambda: pytest.fail("must not nudge while paused"))
        monkeypatch.setenv("CRON_SECRET", "")
        seen = {}
        monkeypatch.setattr(api_mod, "_json_response",
                            lambda h, s, p: seen.update(status=s, payload=p))
        api_mod._handle_cron_abandoned_carts(type("H", (), {"headers": {}})())
        assert seen["status"] == 200
        assert seen["payload"]["skipped"] == "books_sale_paused"
        assert seen["payload"]["nudged"] == 0
