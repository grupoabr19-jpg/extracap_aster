import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from sales_parser import read_sales_records
from sheets_writer import publish_rows, validate_payload


class IngestionContractTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mktemp(suffix=".csv"))
        self.path.write_text(
            "Data;Vendedor;Peso total;Regiao\n"
            "04/09/2026;VENDEDOR A;1.234,56;MICRO\n"
            "05/09/2026;VENDEDOR A;99,00;MICRO\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_daily_rows_reject_future_data(self):
        records = read_sales_records(self.path, date(2026, 9, 4), "Vendedor", "Peso total", "Data")
        self.assertEqual(records, [(date(2026, 9, 4), "VENDEDOR A", Decimal("1234.56"))])

    def test_daily_payload_validates(self):
        headers = ["Data", "Vendedor", "Peso do dia (kg)", "Observacao"]
        rows = [["2026-09-04", "VENDEDOR A", 1234.56, "Automacao Aster"]]
        validate_payload(date(2026, 9, 4), "daily_rows", headers, rows)

    def test_cumulative_payload_validates(self):
        headers = ["Data", "Vendedor", "Peso acumulado (kg)", "Observacao"]
        rows = [["2026-09-04", "VENDEDOR A", 1234.56, "Aster acumulado ate 2026-09-04"]]
        validate_payload(date(2026, 9, 4), "cumulative_by_seller", headers, rows)

    def test_invalid_vendor_and_weight_are_rejected(self):
        headers = ["Data", "Vendedor", "Peso do dia (kg)", "Observacao"]
        with self.assertRaises(ValueError):
            validate_payload(date(2026, 9, 4), "daily_rows", headers, [["2026-09-04", "", 1, ""]])
        with self.assertRaises(ValueError):
            validate_payload(date(2026, 9, 4), "daily_rows", headers, [["2026-09-04", "A", float("inf"), ""]])

    def test_publish_requires_success_status(self):
        response = _FakeResponse(json.dumps({"status": "error", "error": "rejected"}).encode())
        with patch("sheets_writer.urlopen", return_value=response):
            with self.assertRaises(RuntimeError):
                publish_rows("https://example.invalid", "token", "1_Lançamentos Diários", date(2026, 9, 4), "daily_rows", ["Data", "Vendedor", "Peso", "Obs"], [["2026-09-04", "A", 1, ""]])


class _FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        from io import BytesIO
        return BytesIO(self.body)

    def __exit__(self, *args):
        return False


if __name__ == "__main__":
    unittest.main()
