"""Tests for the book-order tracking re-share (DTDC).

After dispatch the bot stores tracking_no + courier_name. When a customer later
asks about their order, the bot re-shares the number + DTDC link instead of
re-opening the book catalog.
"""
import book_bot


DISPATCHED = {
    "order_code": "XTR-20260606-DEFE5E55",
    "courier_name": "DTDC",
    "tracking_no": "D1234567890",
    "status": "dispatched",
}


class TestIsTrackingQuestion:
    def test_courier_question(self):
        assert book_bot.is_tracking_question("What is the courier called?") is True

    def test_track_word(self):
        assert book_bot.is_tracking_question("track my order") is True

    def test_status_word(self):
        assert book_bot.is_tracking_question("order status?") is True

    def test_when_will_i_get_phrase(self):
        assert book_bot.is_tracking_question("when will i get it") is True

    def test_where_is_my_order(self):
        assert book_bot.is_tracking_question("where is my order") is True

    def test_malayalam_when_get(self):
        assert book_bot.is_tracking_question("എന്ന് കിട്ടും") is True

    def test_plain_books_is_not_tracking(self):
        assert book_bot.is_tracking_question("books") is False

    def test_greeting_is_not_tracking(self):
        assert book_bot.is_tracking_question("hi") is False

    def test_empty_is_not_tracking(self):
        assert book_bot.is_tracking_question("") is False


class TestComposeTrackingReply:
    def test_includes_code_number_and_link(self):
        out = book_bot.compose_tracking_reply(DISPATCHED)
        assert "XTR-20260606-DEFE5E55" in out
        assert "D1234567890" in out
        assert book_bot.DTDC_TRACK_URL in out
        assert "DTDC" in out

    def test_no_tracking_number_yet(self):
        order = {**DISPATCHED, "tracking_no": None}
        out = book_bot.compose_tracking_reply(order)
        assert "dispatched" in out.lower()
        assert book_bot.DTDC_TRACK_URL not in out  # no broken "paste this" link

    def test_courier_defaults_to_dtdc(self):
        order = {**DISPATCHED, "courier_name": None}
        assert "DTDC" in book_bot.compose_tracking_reply(order)


class TestMaybeTrackingReply:
    def test_tracking_question_with_dispatched_order_sends(self, monkeypatch):
        sent = []
        monkeypatch.setattr(book_bot._dbc, "get_dispatched_book_order",
                            lambda phone: DISPATCHED)
        monkeypatch.setattr(book_bot, "_send_text",
                            lambda phone, msg: sent.append(msg))
        out = book_bot._maybe_tracking_reply("919999999999", "where is my order")
        assert out == []                       # handled (already sent)
        assert sent and "D1234567890" in sent[0]

    def test_tracking_question_without_order_returns_none(self, monkeypatch):
        monkeypatch.setattr(book_bot._dbc, "get_dispatched_book_order",
                            lambda phone: {})
        out = book_bot._maybe_tracking_reply("919999999999", "where is my order")
        assert out is None                     # falls through to normal routing

    def test_non_tracking_text_returns_none_without_lookup(self, monkeypatch):
        def _boom(phone):
            raise AssertionError("should not look up for non-tracking text")
        monkeypatch.setattr(book_bot._dbc, "get_dispatched_book_order", _boom)
        assert book_bot._maybe_tracking_reply("919999999999", "books") is None
