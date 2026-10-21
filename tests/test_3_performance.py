"""Таблица 3 ТЗ — оценочное тестирование производительности (значения печатаются)."""
import time
import unittest

from datetime import date
from decimal import Decimal

from helpers import CBR, ROOT, params, synthetic
from app.models import CalcParams
from app.models import InsuranceMode
from app.optimization.service import optimize


def measure(n, banks, amount, mode=InsuranceMode.PRINCIPAL, personal=None, K=99):
    t = time.perf_counter()
    r = optimize(synthetic(n, banks), params(amount, mode, K=min(K, banks), personal=personal), CBR, 1_400_000)
    total = (time.perf_counter() - t) * 1000
    print(f"\n    {n} вкладов, {amount:,} р., режим {mode.value}, K={min(K, banks)}, личн.={personal}: "
          f"всего {total:.0f} мс; шаг {r.step} (увеличен: {r.step_increased}); "
          f"точный − приближённый: {r.exact.income - r.approx.income} р.")
    return r, total


class Performance(unittest.TestCase):
    def test_1_50_deposits_1m(self):
        r, total = measure(50, 15, 1_000_000)
        self.assertLess(total, 2000)

    def test_2_200_deposits_100m(self):
        for mode in InsuranceMode:
            with self.subTest(mode=mode.name):
                r, total = measure(200, 50, 100_000_000, mode)
                self.assertLess(total, 2000)
        r, total = measure(200, 50, 100_000_000, InsuranceMode.PRINCIPAL, K=10)
        self.assertLess(total, 2000)

    def test_3_personal_limit_near_amount(self):
        r, total = measure(500, 50, 100_000_000, InsuranceMode.NONE, personal=99_000_000)
        self.assertTrue(r.step_increased)
        self.assertTrue(any("округлены до" in n for n in r.notices))
        self.assertLess(total, 2000)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class LargeCatalog(unittest.TestCase):
    """Справочник с «сеточными» линейками: один продукт банка даёт до 14 записей.

    Проверяется, что расчёт укладывается в 2 с при числе вкладов заметно больше 200
    (п. 4.1.3.5 ТЗ) и что при росте объёма срабатывает укрупнение шага (п. 4.1.2.11).
    """

    @classmethod
    def setUpClass(cls):
        import os
        import sys
        import tempfile
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import generate_catalog
        cls.dir = tempfile.mkdtemp()
        cls.paths = {}
        for rows in (300, 500, 1000):
            path = os.path.join(cls.dir, f"cat_{rows}.db")
            generate_catalog.build(path, rows, banks_count=20)
            cls.paths[rows] = path

    def run_case(self, rows, mode, amount, K=99, personal=None):
        from app.data_management.repository import load_catalog, select_deposits
        catalog = load_catalog(self.paths[rows])
        self.assertFalse(catalog.issues)
        selected = select_deposits(catalog.deposits, amount, 1, 18, frozenset())
        banks = len({d.bank_id for d in selected})
        p = CalcParams(amount, 1, 18, frozenset(), min(K, banks), mode, personal, Decimal("0"),
                       date(2026, 9, 16))
        started = time.perf_counter()
        r = optimize(selected, p, CBR, 1_400_000)
        total = (time.perf_counter() - started) * 1000
        print(f"\n    {rows} вкладов ({len(selected)} отобрано, {banks} банков), режим {mode.value}, "
              f"K={p.max_banks}, {amount:,} р.: всего {total:.0f} мс; шаг {r.step:,} "
              f"(увеличен: {r.step_increased}); точный − приближённый: {r.exact.income - r.approx.income} р.")
        self.assertLess(total, 2000)
        return r, total

    def test_300_rows(self):
        for mode in InsuranceMode:
            with self.subTest(mode=mode.name):
                self.run_case(300, mode, 100_000_000)

    def test_500_rows(self):
        for mode in InsuranceMode:
            with self.subTest(mode=mode.name):
                self.run_case(500, mode, 100_000_000)

    def test_1000_rows(self):
        """Тест 2 таблицы 3 ТЗ: 1000 вкладов, сеточные линейки, 20 банков, три режима."""
        for mode in InsuranceMode:
            with self.subTest(mode=mode.name):
                self.run_case(1000, mode, 100_000_000)
        self.run_case(1000, InsuranceMode.PRINCIPAL, 100_000_000, K=10)
        r, _ = self.run_case(1000, InsuranceMode.NONE, 100_000_000, personal=99_000_000)
        self.assertTrue(r.step_increased)

    def test_greedy_never_beats_exact_in_modes_1_2(self):
        """Точный метод не должен уступать приближённому (режимы 1 и 2 — строго)."""
        for rows in (300, 500, 1000):
            for mode in (InsuranceMode.NONE, InsuranceMode.PRINCIPAL):
                with self.subTest(rows=rows, mode=mode.name):
                    r, _ = self.run_case(rows, mode, 100_000_000)
                    self.assertGreaterEqual(r.exact.income, r.approx.income)
