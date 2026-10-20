"""Проверки отдельных требований ТЗ."""
import os
import sqlite3
import unittest
from datetime import date
from decimal import Decimal

from helpers import CBR, demo_db, dep, params
from app.models import InsuranceMode
from app.optimization.service import REASON_STEP, optimize
from app.ui.routes import create_app
from app.yield_calc import calculator as calc

TODAY = date(2026, 9, 16)
FORM = {"amount": "3000000", "term_from": "1", "term_to": "18", "insurance_mode": "2", "other_income": "0"}


def client():
    return create_app(demo_db(), online=False, today_fn=lambda: TODAY).test_client()


class TechnicalInfo(unittest.TestCase):
    """п. 4.1.3.2, 4.6.2.12, 4.1.1.1 (18): технические сведения — свёрнутый блок и последний раздел файла."""

    def test_collapsed_block_in_page(self):
        html = client().post("/calculate", data=FORM).data.decode()
        start = html.index('<details class="tech">')
        block = html[start:html.index("</details>", start)]
        for text in ("Отклонение результата приближённого метода", "Время решения", "Шаг дискретизации"):
            self.assertIn(text, block)
        totals = html[html.index('<dl class="totals">'):start]
        self.assertNotIn("Шаг дискретизации", totals)
        self.assertNotIn("Время решения", totals)

    def test_last_section_in_file(self):
        c = client()
        c.post("/calculate", data=FORM)
        text = c.get("/save").data.decode()
        tail = text[text.rindex("ТЕХНИЧЕСКИЕ СВЕДЕНИЯ О РАСЧЁТЕ"):]
        self.assertIn("Шаг дискретизации", tail)
        self.assertNotIn("ИТОГОВЫЕ ПОКАЗАТЕЛИ", tail)
        self.assertNotIn("НДФЛ ПО ГОДАМ", tail)


class InsuranceModeNames(unittest.TestCase):
    def test_mode_names(self):
        html = client().get("/").data.decode()
        for name in ("Без учёта лимита", "По вложенной сумме", "По сумме с процентами"):
            self.assertIn(name, html)


class KPlusOne(unittest.TestCase):
    """п. 4.1.3.3: прирост при K+1 — только если K меньше числа неисключённых банков."""

    def deps(self):
        return [dep(i, i, 12, 12, 1000, None) for i in range(1, 5)]

    def test_shown_when_k_less_than_banks(self):
        p = params(10_000_000, InsuranceMode.PRINCIPAL, K=2)
        p.available_banks = 4
        self.assertIsNotNone(optimize(self.deps(), p, CBR, 1_400_000).k_plus_one_gain)

    def test_hidden_when_k_equals_non_excluded(self):
        p = params(10_000_000, InsuranceMode.PRINCIPAL, K=4)
        p.available_banks = 4
        self.assertIsNone(optimize(self.deps(), p, CBR, 1_400_000).k_plus_one_gain)


class NonTaxableFutureMonths(unittest.TestCase):
    """п. 4.1.2.6: для ненаступивших месяцев — последнее полученное значение ключевой ставки."""

    def test_current_year_uses_max_of_known_and_current(self):
        d = dep(1, 1, 20, 3, 1, None)
        pos = calc.build_positions([(d, 1_000_000)], calc.coefficients([d], TODAY), TODAY)
        # известные значения года ниже текущей ставки — берётся текущая
        years, _ = calc.compute_tax(pos, 0, TODAY, 17.0, {2026: 16.0})
        self.assertEqual({t.year: t for t in years}[2026].non_taxable, Decimal("170000.00"))
        # известные значения выше — берётся наибольшее из них
        years, _ = calc.compute_tax(pos, 0, TODAY, 14.0, {2026: 16.0})
        self.assertEqual({t.year: t for t in years}[2026].non_taxable, Decimal("160000.00"))


class AvgMaxRateMissing(unittest.TestCase):
    """п. 4.1.1.2 (5): без средней максимальной ставки проверка правдоподобия не выполняется."""

    def test_no_marks_and_no_crash(self):
        import shutil, tempfile
        path = os.path.join(tempfile.mkdtemp(), "no_avg.db")
        shutil.copy(demo_db(), path)
        con = sqlite3.connect(path)
        con.execute("DELETE FROM cbr_value WHERE name = 'avg_max_rate'")
        con.commit(); con.close()
        c = create_app(path, online=False, today_fn=lambda: TODAY).test_client()
        html = c.post("/calculate", data=dict(FORM, term_from="12", term_to="12", amount="1000000")).data.decode()
        self.assertNotIn("с нетипично высокой ставкой", html)
        self.assertNotIn('class="odd-rate"', html)
        self.assertIn("проверка правдоподобия ставок не выполняется", html)


class SingleDeposit(unittest.TestCase):
    """п. 4.1.2.13: если вся сумма не помещается в один вклад — указывается размещаемая сумма."""

    def test_amount_limited(self):
        deps = [dep(1, 1, 15, 12, 1000, 500_000), dep(2, 2, 12, 12, 1000, None)]
        r = optimize(deps, params(3_000_000, InsuranceMode.PRINCIPAL), CBR, 1_400_000)
        self.assertEqual(r.single_best_amount, 1_400_000)     # не вся сумма: предел по банку
        html = client().post("/calculate", data=dict(FORM, amount="5000000")).data.decode()
        self.assertIn("Всю сумму в один вклад разместить нельзя", html)


class StepAndRemainder(unittest.TestCase):
    def test_step_notice_wording(self):
        """п. 4.1.2.11: сообщение об округлении сумм с точностью до шага."""
        from helpers import synthetic
        r = optimize(synthetic(500, 50), params(100_000_000, InsuranceMode.NONE, K=50, personal=99_000_000),
                     CBR, 1_400_000)
        self.assertTrue(r.step_increased)
        self.assertTrue(any("округлены с точностью до" in n and "шага дискретизации" in n for n in r.notices))

    def test_remainder_equal_to_step_is_discretisation(self):
        """п. 4.1.3.4: остаток, не превышающий шага, — погрешность дискретизации."""
        r = optimize([dep(1, 1, 12, 12, 1000, None)], params(1_001_000 - 1, InsuranceMode.NONE), CBR, 1_400_000)
        self.assertLessEqual(r.remainder, r.step)
        self.assertEqual(r.remainder_reasons[0], REASON_STEP)


if __name__ == "__main__":
    unittest.main(verbosity=2)
