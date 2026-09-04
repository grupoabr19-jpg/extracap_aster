import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from business_calendar import resolve_reference_date


class ReferenceDateTests(unittest.TestCase):
    def test_explicit_date_is_preserved(self):
        self.assertEqual(resolve_reference_date(date(2026, 9, 4)), date(2026, 9, 4))

    def test_default_date_uses_sao_paulo_timezone(self):
        try:
            sao_paulo = ZoneInfo("America/Sao_Paulo")
        except ZoneInfoNotFoundError:
            self.skipTest("tzdata nao instalado no ambiente local")
        with patch("business_calendar.datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 5, 0, 30, tzinfo=sao_paulo)
            self.assertEqual(resolve_reference_date(), date(2026, 9, 4))


if __name__ == "__main__":
    unittest.main()
