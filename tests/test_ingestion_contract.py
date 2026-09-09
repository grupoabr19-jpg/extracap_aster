import json
import logging
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main
from main import _date_balance_payload, build_email_message
from sales_parser import number, read_sales_records
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

    def test_numeric_zero_from_spreadsheet_is_valid(self):
        for value in (0, 0.0, Decimal('0')):
            self.assertEqual(number(value), Decimal('0'))
        for value in (True, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                number(value)

    def test_undated_summary_cannot_be_published_as_multiday_daily_rows(self):
        self.path.write_text('Vendedor;Peso total\nVENDEDOR A;100\n', encoding='utf8')
        with self.assertRaisesRegex(ValueError, 'varios dias'):
            read_sales_records(self.path, date(2026, 9, 4), require_date=True)

    def test_daily_payload_validates(self):
        headers = ["Data", "Vendedor", "Peso do dia (kg)", "Observacao"]
        rows = [["2026-09-04", "VENDEDOR A", 1234.56, "Automacao Aster"]]
        validate_payload(date(2026, 9, 4), "daily_rows", headers, rows)

    def test_cumulative_payload_validates(self):
        headers = ["Data", "Vendedor", "Peso acumulado (kg)", "Observacao"]
        rows = [["2026-09-04", "VENDEDOR A", 1234.56, "Aster acumulado ate 2026-09-04"]]
        validate_payload(date(2026, 9, 4), "cumulative_by_seller", headers, rows)

    def test_date_balance_payload_uses_raw_varejo_summary(self):
        self.path.write_text(
            "Vendedor;Tipo;Regiao;Segmento;Peso total;Valor total\n"
            "VARE - A;VAREJO;MICRO;ESPECIALISTA;1.234,56;R$ 10,00\n"
            "ATF - B;ATACADO;CENTRO;ATACADO;999,00;R$ 20,00\n",
            encoding="utf-8",
        )
        headers, rows = _date_balance_payload(self.path, date(2026, 9, 4))
        self.assertEqual(headers, ["Data", "Vendedor", "Tipo", "Regiao", "Segmento", "Peso do dia (kg)", "Valor total"])
        self.assertEqual(rows, [["2026-09-04", "VARE - A", "VAREJO", "MICRO", "ESPECIALISTA", Decimal("1234.56"), "R$ 10,00"]])
        validate_payload(date(2026, 9, 4), "date_balance", headers, rows)

    def test_invalid_vendor_and_weight_are_rejected(self):
        headers = ["Data", "Vendedor", "Peso do dia (kg)", "Observacao"]
        with self.assertRaises(ValueError):
            validate_payload(date(2026, 9, 4), "daily_rows", headers, [["2026-09-04", "", 1, ""]])
        with self.assertRaises(ValueError):
            validate_payload(date(2026, 9, 4), "daily_rows", headers, [["2026-09-04", "A", float("inf"), ""]])
        with self.assertRaises(ValueError):
            validate_payload(date(2026, 9, 4), "daily_rows", headers, [["2026-02-31", "A", 1, ""]])
        with self.assertRaises(ValueError):
            validate_payload(date(2026, 9, 4), "daily_rows", headers, [["2026-09-04", "A", True, ""]])
        raw_headers = ["Data", "Vendedor", "Tipo", "Regiao", "Segmento", "Peso do dia (kg)", "Valor total"]
        with self.assertRaises(ValueError):
            validate_payload(date(2026, 9, 4), "date_balance", raw_headers, [["2026-09-04", "ATF - B", "ATACADO", "CENTRO", "ATACADO", 999, ""]])

    def test_publish_requires_success_status(self):
        response = _FakeResponse(json.dumps({"status": "error", "error": "rejected"}).encode())
        with patch("sheets_writer.urlopen", return_value=response):
            with self.assertRaises(RuntimeError):
                publish_rows("https://example.invalid", "token", "1_Lançamentos Diários", date(2026, 9, 4), "daily_rows", ["Data", "Vendedor", "Peso", "Obs"], [["2026-09-04", "A", 1, ""]])


    def test_email_template_includes_daily_monthly_rankings_and_link(self):
        settings = SimpleNamespace(
            mail_from="grupoabr19@gmail.com",
            mail_to=["thiago@example.com"],
            mail_cc=[],
            mail_subject="Fallback",
        )
        rows = [
            ["2026-09-04", "VENDEDOR B", "VAREJO", "SUL", "A", Decimal("250"), "R$ 1"],
            ["2026-09-04", "VENDEDOR A", "VAREJO", "SUL", "A", Decimal("300"), "R$ 1"],
            ["2026-09-01", "VENDEDOR B", "VAREJO", "SUL", "A", Decimal("100"), "R$ 1"],
        ]
        message = build_email_message(settings, date(2026, 9, 4), rows)
        body = message.get_body(preferencelist=("plain",)).get_content()
        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("Resultado por vendedor em 04/09/2026", body)
        self.assertIn("Ranking acumulado desde 01/09/2026", body)
        self.assertIn("1. VENDEDOR B: 0,35 t", body)
        self.assertIn("2. VENDEDOR A: 0,30 t", body)
        self.assertIn("Acessar ranking geral", html)
        self.assertIn("docs.google.com/spreadsheets", html)

    def test_date_balance_run_keeps_published_rows_for_email(self):
        self.path.write_text(
            "Vendedor;Tipo;Regiao;Segmento;Peso total;Valor total\n"
            "VARE - A;VAREJO;MICRO;ESPECIALISTA;1.234,56;R$ 10,00\n",
            encoding="utf-8",
        )
        settings = SimpleNamespace(
            headless=True,
            output_dir=self.path.parent,
            log_dir=self.path.parent,
            daily_comparison_enabled=True,
            report_data_mode="date_balance",
            sales_vendor_column="",
            sales_quantity_column="",
            sales_date_column="",
            mail_to=["thiago@example.com"],
        )
        captured = {}

        def fake_build_email_message(_settings, _reference_date, data_rows, *_args):
            captured["data_rows"] = data_rows
            message = EmailMessage()
            message["From"] = "from@example.com"
            message["To"] = "to@example.com"
            message["Subject"] = "ok"
            message.set_content("ok")
            return message

        with (
            patch.object(main.Settings, "from_env", return_value=settings),
            patch.object(main, "configure_logging", return_value=logging.getLogger("test-aster")),
            patch.object(main, "sync_playwright", return_value=_FakePlaywrightContext()),
            patch.object(main, "login_and_extract", return_value=("", self.path)),
            patch.object(main, "publish_from_env"),
            patch.object(main, "_sheet_values_for_email", return_value=[]),
            patch.object(main, "build_email_message", side_effect=fake_build_email_message),
            patch.object(main, "_write_rendered_email_json"),
            patch.object(main, "send_email"),
        ):
            main.run(date(2026, 9, 4))

        self.assertEqual(
            captured["data_rows"],
            [["2026-09-04", "VARE - A", "VAREJO", "MICRO", "ESPECIALISTA", Decimal("1234.56"), "R$ 10,00"]],
        )


class _FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        from io import BytesIO
        return BytesIO(self.body)

    def __exit__(self, *args):
        return False


class _FakePlaywrightContext:
    def __enter__(self):
        return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **_kwargs: _FakeBrowser()))

    def __exit__(self, *args):
        return False


class _FakeBrowser:
    def new_page(self):
        return object()

    def new_context(self, **_kwargs):
        return _FakeBrowserContext()

    def close(self):
        pass


class _FakeBrowserContext:
    def new_page(self):
        return object()

    def close(self):
        pass


if __name__ == "__main__":
    unittest.main()
