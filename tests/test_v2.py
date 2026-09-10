from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from v2.engine import aggregate, automatic_rows, prepare_rows, rankings
from v2.report_parser import normalize_sales, parse_csv
from v2.sheets import Target

class V2Tests(unittest.TestCase):
    def setUp(self):
        self.targets = [Target("HELOÁ", "BRAG. PTA.", Decimal("90.576"), 21), Target("LEIZ", "BRAG. PTA.", Decimal("109.598"), 21)]

    def sales(self):
        report = Path(__file__).parent / "fixtures" / "resumo_comercial.csv"
        return normalize_sales(parse_csv(report), [target.vendor for target in self.targets])

    def test_parser_and_token_matching(self):
        sales = self.sales()
        self.assertEqual([sale.vendor for sale in sales], ["HELOÁ", "LEIZ"])
        self.assertEqual(sales[0].weight_kg, Decimal("31439.29"))

    def test_prepare_replaces_only_automatic_rows(self):
        existing = [["Data", "Vendedor", "Peso do dia (kg)", "Observação", "Região (automática)", "Faturamento (R$)"], [date(2026, 9, 8), "HELOÁ", 1000, "ASTER - saldo de 08/09/2026", "BRAG. PTA.", 100], [date(2026, 9, 9), "HELOÁ", 900, "MANUAL", "BRAG. PTA.", 90]]
        rows, conflicts = prepare_rows(existing, self.sales(), self.targets, date(2026, 9, 9))
        self.assertEqual(len(rows), 5)
        self.assertTrue(any("HELOÁ" in item for item in conflicts))
        self.assertTrue(any(row[3] == "MANUAL" for row in rows))

    def test_publish_payload_contains_only_new_automatic_snapshot(self):
        sales = self.sales()
        rows = automatic_rows(sales, self.targets, date(2026, 9, 9))
        self.assertEqual(len(rows), len(sales))
        self.assertEqual(rows[0][2], 31439.29)
        self.assertTrue(all(row[3] == "ASTER - saldo de 09/09/2026" for row in rows))
        self.assertTrue(all(row[1] != "MANUAL" for row in rows))

    def test_aggregate_and_ranking(self):
        rows = [["Data", "Vendedor", "Peso do dia (kg)", "Observação", "Região (automática)", "Faturamento (R$)"], [date(2026, 9, 9), "HELOÁ", 31439.29, "ASTER - saldo de 09/09/2026", "BRAG. PTA.", "216929.37"]]
        daily, monthly, _ = aggregate(rows, date(2026, 9, 9), self.targets)
        self.assertEqual(daily["HELOÁ"], Decimal("31439.29"))
        self.assertEqual(rankings(monthly, self.targets)[0]["vendor"], "HELOÁ")

if __name__ == "__main__": unittest.main()
