"""Дополнительно: контроль целостности справочника, расчёт дохода и НДФЛ, кеш сведений ЦБ."""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from helpers import ROOT, dep
from app.data_management.repository import load_catalog
from app.external_sources import cbr_client
from app.yield_calc import calculator as calc

sys.path.insert(0, os.path.join(ROOT, "tools"))
import init_db  # noqa: E402


class Integrity(unittest.TestCase):
    def test_bad_records_reported_and_skipped(self):
        path = os.path.join(tempfile.mkdtemp(), "t.db")
        init_db.build(path)
        con = sqlite3.connect(path)
        con.executescript("""
          INSERT INTO deposit(bank_id,name,rate,term_months,min_sum,max_sum,capitalization,actual_date) VALUES
           (1,'Ноль',0,6,1000,NULL,0,'2026-09-01'),
           (1,'МинБольшеМакс',10,6,5000,1000,0,'2026-09-01'),
           (1,'Срок24',10,24,1000,NULL,0,'2026-09-01'),
           (1,'',10,6,1000,NULL,0,'2026-09-01'),
           (1,'БезДаты',10,6,1000,NULL,0,NULL),
           (99,'БезБанка',10,6,1000,NULL,0,'2026-09-01');""")
        con.commit(); con.close()
        cat = load_catalog(path)
        text = " | ".join(i.problem for i in cat.issues)
        for fragment in ["положительной", "превышает максимальную", "вне диапазона",
                         "наименование вклада", "дата актуальности", "нет банка"]:
            self.assertIn(fragment, text)
        self.assertEqual(len(cat.deposits), 19)


class Income(unittest.TestCase):
    def test_simple_365(self):
        d = dep(1, 1, 19, 12, 1, None)
        self.assertEqual(calc.position_income(100000, calc.income_coefficient(d, date(2026, 9, 16))),
                         Decimal("19000.00"))

    def test_leap_year_split(self):
        # 01.07.2027 – 01.07.2028: 184 дня 2027 г. /365 + 182 дня 2028 г. /366
        d = dep(1, 1, 10, 12, 1, None)
        expected = 0.10 * (184 / 365 + 182 / 366)
        self.assertAlmostEqual(calc.income_coefficient(d, date(2027, 7, 1)), expected, places=12)

    def test_capitalization_monthly(self):
        d = dep(1, 1, 12, 3, 1, None, cap=True)
        f = (1 + .12 * 30 / 365) * (1 + .12 * 31 / 365) * (1 + .12 * 30 / 365) - 1   # 16.09→16.12
        self.assertAlmostEqual(calc.income_coefficient(d, date(2026, 9, 16)), f, places=12)

    def test_capitalization_income_split_by_year(self):
        """п. 4.1.2.6: проценты вклада с капитализацией относятся к годам начисления."""
        d = dep(1, 1, 12, 6, 1, None, cap=True)      # 16.09.2026 → 16.03.2027
        pos = calc.build_positions([(d, 1_000_000)], calc.coefficients([d], date(2026, 9, 16)),
                                   date(2026, 9, 16))[0]
        self.assertEqual(sorted(pos.income_by_year), [2026, 2027])
        self.assertEqual(sum(pos.income_by_year.values()), pos.income)
        self.assertGreater(pos.income_by_year[2026], Decimal("0"))

    def test_no_capitalization_income_in_final_year(self):
        d = dep(1, 1, 12, 6, 1, None)
        pos = calc.build_positions([(d, 1_000_000)], calc.coefficients([d], date(2026, 9, 16)),
                                   date(2026, 9, 16))[0]
        self.assertEqual(pos.income_by_year, {2027: pos.income})

    def test_tax_is_increment_only(self):
        """п. 4.1.2.6: выводится НДФЛ, приходящийся на доход по вкладам."""
        d = dep(1, 1, 20, 3, 1, None)                # доход в текущем году
        pos = calc.build_positions([(d, 2_000_000)], calc.coefficients([d], date(2026, 9, 16)),
                                   date(2026, 9, 16))
        years, total = calc.compute_tax(pos, 500_000, date(2026, 9, 16), 14.0, {2026: 16.0})
        y = [t for t in years if t.year == 2026][0]
        self.assertEqual(y.tax, y.tax_full - y.tax_without)
        self.assertEqual(total, sum(t.tax for t in years))
        # тот же расчёт без прочего дохода: НДФЛ на вклады не больше полного налога
        _, only_deposits = calc.compute_tax(pos, 0, date(2026, 9, 16), 14.0, {2026: 16.0})
        self.assertLessEqual(total, only_deposits + y.tax_without)

    def test_tax_by_year_of_end(self):
        d1, d2 = dep(1, 1, 20, 3, 1, None), dep(2, 2, 20, 12, 1, None)
        pos = calc.build_positions([(d1, 2_000_000), (d2, 1_000_000)],
                                   calc.coefficients([d1, d2], date(2026, 9, 16)), date(2026, 9, 16))
        years, total = calc.compute_tax(pos, 50_000, date(2026, 9, 16), 14.0, {2026: 16.0})
        y = {t.year: t for t in years}
        self.assertEqual(y[2026].non_taxable, Decimal("160000.00"))     # макс. ставка 2026 г.
        self.assertEqual(y[2027].deposit_income, pos[1].income)         # вклад без капитализации
        self.assertEqual(y[2027].non_taxable, Decimal("140000.00"))     # текущая — для будущего года
        self.assertEqual(y[2026].other_income, Decimal("50000"))
        self.assertEqual(total, sum(t.tax for t in years))


class CbrCache(unittest.TestCase):
    def test_offline_uses_cache(self):
        path = os.path.join(tempfile.mkdtemp(), "t.db")
        init_db.build(path)
        old = cbr_client.SOAP_URL
        cbr_client.SOAP_URL = "http://127.0.0.1:9/unreachable"
        try:
            info = cbr_client.get_cbr_info(path, date(2026, 9, 16), online=True)
        finally:
            cbr_client.SOAP_URL = old
        self.assertTrue(info.from_cache)
        self.assertIn("недоступен", info.message)
        self.assertEqual(info.key_rate_current, 14.0)

    def test_suspicious(self):
        from app.data_management.repository import is_suspicious   # п. 4.2.4 — управление данными
        self.assertTrue(is_suspicious(17.5, 12.948))
        self.assertFalse(is_suspicious(15.2, 12.948))


if __name__ == "__main__":
    unittest.main(verbosity=2)
