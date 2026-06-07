"""Tests for store_digest — opening/closing PC messages with day/week/month logs.

Working week is Mon-Sat (Sunday closed), so:
- last working day of the WEEK  = Saturday
- last working day of the MONTH = the last non-Sunday date of the month

Date anchors used below (verified against the project calendar where
2026-06-07 is a Sunday):
- 2026-06-08 Mon, 2026-06-13 Sat, 2026-06-30 Tue (last working day of June)
- 2026-05-30 Sat is the last working day of May 2026 (2026-05-31 is a Sunday)
"""
from datetime import date

import store_digest as sd


DAILY = {"date": "2026-06-08", "total_jobs": 5, "completed": 4,
         "pending": 1, "revenue": 1200, "cash": 700, "upi": 500}


class TestWorkingDayLogic:
    def test_saturday_is_last_working_day_of_week(self):
        assert sd.is_last_working_day_of_week(date(2026, 6, 13)) is True

    def test_friday_is_not_last_working_day_of_week(self):
        assert sd.is_last_working_day_of_week(date(2026, 6, 12)) is False

    def test_sunday_is_not_last_working_day_of_week(self):
        assert sd.is_last_working_day_of_week(date(2026, 6, 7)) is False

    def test_month_ending_on_weekday(self):
        # June 2026 ends Tue 30th -> that is the last working day.
        assert sd.is_last_working_day_of_month(date(2026, 6, 30)) is True
        assert sd.is_last_working_day_of_month(date(2026, 6, 29)) is False

    def test_month_ending_on_sunday_rolls_back_to_saturday(self):
        # May 2026 ends Sun 31st; last working day is Sat 30th.
        assert sd.is_last_working_day_of_month(date(2026, 5, 30)) is True
        assert sd.is_last_working_day_of_month(date(2026, 5, 31)) is False
        assert sd.is_last_working_day_of_month(date(2026, 5, 29)) is False


class TestDailyLog:
    def test_contains_jobs_and_revenue(self):
        out = sd.format_daily_log(DAILY)
        assert "5" in out                 # total jobs
        assert "1,200" in out             # revenue
        assert "700" in out and "500" in out  # cash / upi split


class TestOpeningMessage:
    def test_opening_has_date_and_online(self):
        out = sd.compose_opening_message(date(2026, 6, 8))
        assert "online" in out.lower()
        assert "2026" in out


class TestClosingMessage:
    def test_weekday_close_has_only_daily(self):
        # Monday: not Saturday, not last working day of month.
        out = sd.compose_closing_message(
            date(2026, 6, 8), DAILY, weekly_rows=[DAILY], monthly_rows=[DAILY])
        assert "Week summary" not in out
        assert "Month summary" not in out

    def test_saturday_close_includes_weekly(self):
        out = sd.compose_closing_message(
            date(2026, 6, 13), DAILY, weekly_rows=[DAILY, DAILY], monthly_rows=[DAILY])
        assert "Week summary" in out
        assert "Month summary" not in out  # 13th is not last working day of June

    def test_last_working_day_of_month_includes_monthly(self):
        # Tue 2026-06-30: last working day of month, not a Saturday.
        out = sd.compose_closing_message(
            date(2026, 6, 30), DAILY, weekly_rows=[DAILY], monthly_rows=[DAILY, DAILY])
        assert "Month summary" in out
        assert "Week summary" not in out

    def test_saturday_that_is_also_month_end_includes_both(self):
        # Sat 2026-05-30: last working day of week AND of month.
        out = sd.compose_closing_message(
            date(2026, 5, 30), DAILY, weekly_rows=[DAILY], monthly_rows=[DAILY])
        assert "Week summary" in out
        assert "Month summary" in out

    def test_unclean_shutdown_flag_changes_wording(self):
        clean = sd.compose_closing_message(date(2026, 6, 8), DAILY, clean=True)
        dirty = sd.compose_closing_message(date(2026, 6, 8), DAILY, clean=False)
        assert clean != dirty
        assert "offline" in dirty.lower()
