"""
Tests for whatsapp_bot.py — pure logic (message builders, maps, helpers)
No DB or network needed.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import whatsapp_bot as bot


# ─────────────────────────────────────────────────────────────────────────────
# Message builder: return type and key content
# ─────────────────────────────────────────────────────────────────────────────

class TestMsgBuilders:
    def test_step1_size_returns_str(self):
        assert isinstance(bot.msg_step1_size(), str)

    def test_step1_size_mentions_a4(self):
        assert "A4" in bot.msg_step1_size()

    def test_step1_size_mentions_a3(self):
        assert "A3" in bot.msg_step1_size()

    def test_step1_size_has_options_1_2_3(self):
        msg = bot.msg_step1_size()
        assert "1" in msg and "2" in msg and "3" in msg

    def test_step2_colour_returns_str(self):
        assert isinstance(bot.msg_step2_colour(), str)

    def test_step2_colour_mentions_bw_and_colour(self):
        msg = bot.msg_step2_colour().lower()
        assert "black" in msg or "b&w" in msg or "bw" in msg
        assert "colour" in msg or "color" in msg

    def test_step3_layout_returns_str(self):
        assert isinstance(bot.msg_step3_layout(), str)

    def test_step3_layout_mentions_single_and_double(self):
        msg = bot.msg_step3_layout().lower()
        assert "single" in msg
        assert "double" in msg

    def test_step3b_multiup_returns_str(self):
        assert isinstance(bot.msg_step3b_multiup(), str)

    def test_step3b_multiup_mentions_2_and_4(self):
        msg = bot.msg_step3b_multiup()
        assert "2" in msg and "4" in msg

    def test_step3c_multiup_sided_returns_str(self):
        assert isinstance(bot.msg_step3c_multiup_sided(), str)

    def test_step4_copies_returns_str(self):
        assert isinstance(bot.msg_step4_copies(), str)

    def test_step5_finishing_returns_str(self):
        assert isinstance(bot.msg_step5_finishing(), str)

    def test_step5_finishing_mentions_spiral(self):
        assert "spiral" in bot.msg_step5_finishing().lower()

    def test_step6_delivery_returns_str(self):
        assert isinstance(bot.msg_step6_delivery(), str)

    def test_step6_delivery_mentions_delivery_and_pickup(self):
        msg = bot.msg_step6_delivery().lower()
        assert "deliver" in msg or "pickup" in msg or "collect" in msg


class TestMsgStaffQuoteNeeded:
    def test_returns_str(self):
        msg = bot.msg_staff_quote_needed("OSP-001", "Spiral binding", 54.0)
        assert isinstance(msg, str)

    def test_contains_job_id(self):
        msg = bot.msg_staff_quote_needed("OSP-001", "Spiral binding", 54.0)
        assert "OSP-001" in msg

    def test_contains_finishing_label(self):
        msg = bot.msg_staff_quote_needed("OSP-001", "Wiro binding", 30.0)
        assert "Wiro" in msg

    def test_contains_print_cost(self):
        msg = bot.msg_staff_quote_needed("OSP-001", "Spiral", 99.0)
        assert "99" in msg


class TestMsgPaymentLink:
    def test_returns_str(self):
        msg = bot.msg_payment_link("OSP-001", "10 sheets × Rs.3", "https://rzp.io/x", 60)
        assert isinstance(msg, str)

    def test_contains_job_id(self):
        msg = bot.msg_payment_link("OSP-001", "10 sheets", "https://rzp.io/x")
        assert "OSP-001" in msg

    def test_contains_pay_url(self):
        url = "https://rzp.io/unique_link"
        msg = bot.msg_payment_link("OSP-001", "breakdown", url)
        assert url in msg

    def test_contains_breakdown(self):
        msg = bot.msg_payment_link("OSP-001", "20 sheets × Rs.10 = Rs.200", "https://rzp.io/x")
        assert "200" in msg


class TestMsgOtherSize:
    def test_returns_str(self):
        assert isinstance(bot.msg_other_size("OSP-001"), str)

    def test_contains_job_id(self):
        assert "OSP-001" in bot.msg_other_size("OSP-001")


class TestMsgBatchConfirm:
    def _saved(self):
        return {"size": "A4", "colour": "bw", "layout": "single",
                "copies": 1, "finishing": "none", "delivery": 0}

    def test_returns_str(self):
        msg = bot.msg_batch_confirm(1, 3, "report.pdf", 10, self._saved())
        assert isinstance(msg, str)

    def test_shows_file_number(self):
        msg = bot.msg_batch_confirm(1, 3, "report.pdf", 10, self._saved())
        assert "1" in msg and "3" in msg

    def test_shows_filename(self):
        msg = bot.msg_batch_confirm(1, 3, "myfile.pdf", 10, self._saved())
        assert "myfile" in msg

    def test_first_file_of_many(self):
        # job_index=0 → "File 1 of 2"
        msg = bot.msg_batch_confirm(0, 2, "file.pdf", 5, self._saved())
        assert "1" in msg and "2" in msg


class TestMsgBatchFileHeader:
    def test_returns_str(self):
        msg = bot.msg_batch_file_header(2, 5, "doc.pdf", 12)
        assert isinstance(msg, str)

    def test_shows_index_and_total(self):
        msg = bot.msg_batch_file_header(2, 5, "doc.pdf", 12)
        assert "2" in msg and "5" in msg


class TestMsgBatchSummary:
    def _jobs(self, n=1):
        return [{"job_id": f"OSP-00{i}", "filename": f"file{i}.pdf",
                 "breakdown_short": "10 sheets BW", "amount": 30.0}
                for i in range(1, n+1)]

    def test_returns_str(self):
        msg = bot.msg_batch_summary(self._jobs(), False, 30.0, "https://rzp.io/x")
        assert isinstance(msg, str)

    def test_contains_total(self):
        msg = bot.msg_batch_summary(self._jobs(), False, 99.0, "https://rzp.io/x")
        assert "99" in msg

    def test_contains_pay_url(self):
        url = "https://rzp.io/paylink"
        msg = bot.msg_batch_summary(self._jobs(), False, 50.0, url)
        assert url in msg

    def test_delivery_line_shown(self):
        msg = bot.msg_batch_summary(self._jobs(), True, 80.0, "https://rzp.io/x")
        assert "30" in msg or "delivery" in msg.lower()

    def test_multi_job_shows_filenames(self):
        msg = bot.msg_batch_summary(self._jobs(2), False, 60.0, "https://rzp.io/x")
        assert "file1.pdf" in msg
        assert "file2.pdf" in msg


# ─────────────────────────────────────────────────────────────────────────────
# Input maps
# ─────────────────────────────────────────────────────────────────────────────

class TestInputMaps:
    def test_size_map_a4(self):
        assert bot.SIZE_MAP["1"] == "A4"

    def test_size_map_a3(self):
        assert bot.SIZE_MAP["2"] == "A3"

    def test_size_map_other(self):
        assert bot.SIZE_MAP["3"] == "other"

    def test_colour_map_bw(self):
        assert bot.COLOUR_MAP["1"] == "bw"

    def test_colour_map_col(self):
        assert bot.COLOUR_MAP["2"] == "col"

    def test_layout_map_single(self):
        assert bot.LAYOUT_MAP["1"] == "single"

    def test_layout_map_double(self):
        assert bot.LAYOUT_MAP["2"] == "double"

    def test_layout_map_multiup(self):
        assert bot.LAYOUT_MAP["3"] == "multiup"

    def test_multiup_map_2up(self):
        assert bot.MULTIUP_MAP["1"] == "2up"

    def test_multiup_map_4up(self):
        assert bot.MULTIUP_MAP["2"] == "4up"

    def test_sided_map_single(self):
        assert bot.SIDED_MAP["1"] == "single"

    def test_sided_map_double(self):
        assert bot.SIDED_MAP["2"] == "double"

    def test_finishing_map_none(self):
        assert bot.FINISHING_MAP["1"] == "none"

    def test_finishing_map_spiral(self):
        assert bot.FINISHING_MAP["3"] == "spiral"

    def test_finishing_map_keys_are_strings(self):
        for k in bot.FINISHING_MAP:
            assert isinstance(k, str)


# ─────────────────────────────────────────────────────────────────────────────
# _layout_short
# ─────────────────────────────────────────────────────────────────────────────

class TestLayoutShort:
    def test_single(self):
        assert bot._layout_short("single") == "Single"

    def test_double(self):
        assert bot._layout_short("double") == "Double"

    def test_unknown_passthrough(self):
        assert bot._layout_short("2up") == "2up"


# ─────────────────────────────────────────────────────────────────────────────
# _build_job_settings
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildJobSettings:
    def _session(self, **overrides):
        base = {
            "size": "A4", "colour": "bw", "layout": "single",
            "copies": 2, "finishing": "spiral",
            "multiup_per": None, "multiup_sided": None,
        }
        base.update(overrides)
        return base

    def _job(self):
        return {"page_count": 20, "filename": "test.pdf", "job_id": "OSP-001"}

    def test_returns_dict(self):
        r = bot._build_job_settings(self._session(), self._job())
        assert isinstance(r, dict)

    def test_picks_up_size(self):
        r = bot._build_job_settings(self._session(size="A3"), self._job())
        assert r["size"] == "A3"

    def test_picks_up_copies(self):
        r = bot._build_job_settings(self._session(copies=5), self._job())
        assert r["copies"] == 5

    def test_multiup_layout_resolved(self):
        r = bot._build_job_settings(
            self._session(layout="multiup", multiup_per="4up"),
            self._job()
        )
        assert r["layout"] == "4up"

    def test_page_count_from_job(self):
        r = bot._build_job_settings(self._session(), {"page_count": 42, "filename": "x.pdf", "job_id": "OSP-002"})
        assert r["page_count"] == 42

    def test_job_id_included(self):
        r = bot._build_job_settings(self._session(), self._job())
        assert r["job_id"] == "OSP-001"


class TestBuildJobSettingsFromSaved:
    def _saved(self, **overrides):
        base = {
            "size": "A4", "colour": "col", "layout": "double",
            "copies": 3, "finishing": "none",
        }
        base.update(overrides)
        return base

    def _job(self):
        return {"page_count": 10, "filename": "saved.pdf", "job_id": "OSP-003"}

    def test_uses_saved_colour(self):
        r = bot._build_job_settings_from_saved(self._saved(colour="col"), self._job())
        assert r["colour"] == "col"

    def test_uses_saved_copies(self):
        r = bot._build_job_settings_from_saved(self._saved(copies=7), self._job())
        assert r["copies"] == 7

    def test_sided_defaults_to_single(self):
        r = bot._build_job_settings_from_saved(self._saved(), self._job())
        assert r["sided"] == "single"
