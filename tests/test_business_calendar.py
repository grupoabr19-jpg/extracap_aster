from datetime import date
from unittest import TestCase

from business_calendar import parse_non_working_dates, previous_business_day


class BusinessCalendarTests(TestCase):
    def test_monday_returns_friday(self):
        self.assertEqual(
            previous_business_day(date(2026, 9, 7)),
            date(2026, 9, 4),
        )

    def test_holiday_is_skipped(self):
        holidays = parse_non_working_dates("2026-09-07")
        self.assertEqual(
            previous_business_day(date(2026, 9, 8), holidays),
            date(2026, 9, 4),
        )

    def test_accepts_brazilian_date_format(self):
        self.assertEqual(
            parse_non_working_dates("07/09/2026"),
            {date(2026, 9, 7)},
        )
