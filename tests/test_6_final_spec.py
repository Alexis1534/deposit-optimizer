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


class NoTechnicalInfo(unittest.TestCase):
    """ТЗ ред. 19: технические сведения о расчёте не выводятся ни на странице, ни в файле."""

    def test_not_in_page_and_file(self):
        c = client()
        html = c.post("/calculate", data=FORM).data.decode()
        self.assertNotIn('class="tech"', html)
        for text in ("Технические сведения", "Время решения", "Отклонение результата"):
            self.assertNotIn(text, html)
        text = c.get("/save").data.decode()
        self.assertNotIn("ТЕХНИЧЕСКИЕ СВЕДЕНИЯ", text)
        self.assertNotIn("Время решения", text)


class ExactControlledByGreedy(unittest.TestCase):
    """п. 4.1.1.4 (3): результат точного метода не уступает приближённому."""

    def test_exact_not_worse(self):
        from helpers import synthetic
        for mode in InsuranceMode:
            r = optimize(synthetic(300, 12, seed=7), params(30_000_000, mode, K=5), CBR, 1_400_000)
            self.assertGreaterEqual(r.exact.income, r.approx.income, mode)


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
    """п. 4.1.2.13: вся сумма — в один вклад; страховой лимит и предельная сумма в один банк
    не применяются, выводится застрахованная и незастрахованная части."""

    def test_whole_sum_ignores_insurance_limit(self):
        deps = [dep(1, 1, 15, 12, 1000, 500_000), dep(2, 2, 12, 12, 1000, None)]
        r = optimize(deps, params(3_000_000, InsuranceMode.PRINCIPAL), CBR, 1_400_000)
        self.assertEqual(r.single_best_deposit.deposit_id, 2)    # вклад 1 всю сумму не примет
        self.assertEqual(r.single_best_insured, 1_400_000)
        self.assertEqual(r.single_best_insured + r.single_best_uninsured,
                         3_000_000 + r.single_best_income)
        self.assertTrue(r.single_best_breaks_limits)

    def test_single_may_beat_distribution_with_limits(self):
        deps = [dep(1, 1, 18, 12, 1000, None), dep(2, 2, 10, 12, 1000, None)]
        r = optimize(deps, params(3_000_000, InsuranceMode.PRINCIPAL), CBR, 1_400_000)
        self.assertLess(r.single_best_diff, 0)
        r = optimize(deps, params(3_000_000, InsuranceMode.NONE), CBR, 1_400_000)
        self.assertGreaterEqual(r.single_best_diff, 0)
        self.assertFalse(r.single_best_breaks_limits)

    def test_no_deposit_takes_whole_sum(self):
        deps = [dep(1, 1, 15, 12, 1000, 500_000), dep(2, 2, 12, 12, 1000, 800_000)]
        r = optimize(deps, params(1_000_000, InsuranceMode.NONE), CBR, 1_400_000)
        self.assertIsNone(r.single_best_deposit)
        self.assertEqual(r.single_widest_deposit.deposit_id, 2)

    def test_page_and_file(self):
        c = client()
        html = c.post("/calculate", data=dict(FORM, amount="5000000")).data.decode()
        self.assertIn("Если положить всё на один вклад", html)
        self.assertIn("не застраховано", html)
        text = c.get("/save").data.decode()
        self.assertIn("Если положить всё на один вклад", text)
        self.assertIn("не застраховано", text)


class StepAndRemainder(unittest.TestCase):
    def test_step_notice_wording(self):
        """п. 4.1.2.11: сообщение об округлении сумм с точностью до шага."""
        from helpers import synthetic
        r = optimize(synthetic(500, 50), params(100_000_000, InsuranceMode.NONE, K=50, personal=99_000_000),
                     CBR, 1_400_000)
        self.assertTrue(r.step_increased)
        self.assertTrue(any("округлены до" in n and "Это не ошибка" in n for n in r.notices))

    def test_remainder_equal_to_step_is_discretisation(self):
        """п. 4.1.3.4: остаток, не превышающий шага, — погрешность дискретизации."""
        r = optimize([dep(1, 1, 12, 12, 1000, None)], params(1_001_000 - 1, InsuranceMode.NONE), CBR, 1_400_000)
        self.assertLessEqual(r.remainder, r.step)
        self.assertEqual(r.remainder_reasons[0], REASON_STEP)


class PersonalLimitReplacesInsurance(unittest.TestCase):
    """п. 4.1.2.8, 4.6.2.4: предельная сумма в один банк заменяет режимы страхового лимита."""

    def per_bank(self, r):
        totals = {}
        for p in r.exact.positions:
            totals[p.deposit.bank_id] = totals.get(p.deposit.bank_id, 0) + p.amount
        return totals

    def test_limit_above_insurance_is_used(self):
        deps = [dep(1, 1, 18.0, 12, 1000, None), dep(2, 2, 10.0, 12, 1000, None)]
        for mode in InsuranceMode:
            r = optimize(deps, params(5_000_000, mode, personal=3_000_000), CBR, 1_400_000)
            self.assertEqual(self.per_bank(r)[1], 3_000_000, mode)
            self.assertTrue(r.single_best_breaks_limits)

    def test_limit_below_insurance(self):
        deps = [dep(1, 1, 18.0, 12, 1000, None), dep(2, 2, 10.0, 12, 1000, None)]
        r = optimize(deps, params(2_000_000, InsuranceMode.WITH_INTEREST, personal=1_390_000), CBR, 1_400_000)
        self.assertEqual(self.per_bank(r)[1], 1_390_000)

    def test_form_modes_disabled_and_restored(self):
        c = client()
        data = dict(FORM, insurance_mode_saved="3", personal_enabled="on", personal_limit="2 000 000")
        del data["insurance_mode"]
        html = c.post("/calculate", data=data).data.decode()
        self.assertNotIn('class="has-error', html[:html.index('id="f-personal_limit"')])
        start = html.index('id="f-insurance_mode"') - 60
        fs = html[start:html.index("</fieldset>", start)]
        self.assertIn("is-off", fs)
        self.assertNotIn("checked", fs)
        self.assertIn('name="insurance_mode_saved" id="insurance_mode_saved" value="3"', html)
        text = c.get("/save").data.decode("utf-8-sig", errors="replace")
        self.assertIn("не применяется — задана предельная сумма в один банк", text)


class LeadingZeros(unittest.TestCase):
    """Незначащие нули в начале чисел убираются при расчёте."""

    def test_cleaned_in_form(self):
        from app.ui.validation import _without_leading_zeros as clean
        cases = {"007": "7", "0 001 000": "1 000", "0001000": "1 000", "000": "0", "0": "0",
                 "00,50": "0,50", "0001500.5": "1 500.5", "": "", "12а": "12а", "10": "10"}
        for raw, expected in cases.items():
            self.assertEqual(clean(raw), expected, raw)

    def test_page_shows_cleaned_values(self):
        data = dict(FORM, amount="0003000000", term_from="01", term_to="018", other_income="000,50")
        html = client().post("/calculate", data=data).data.decode()
        for name, value in (("amount", "3 000 000"), ("term_from", "1"), ("term_to", "18"),
                            ("other_income", "0,50")):
            self.assertRegex(html, rf'name="{name}"[^>]*value="{value}"')


class RemainderTips(unittest.TestCase):
    """Подсказки, как уменьшить нераспределённый остаток."""

    def tips(self, **form):
        c = client()
        c.post("/calculate", data=dict(FORM, **form))
        html = c.get("/").data.decode()
        text = c.get("/save").data.decode()
        return html, text

    def test_bank_limit_and_k(self):
        html, text = self.tips(amount="10000000", max_banks="2")
        self.assertIn("Как уменьшить нераспределённый остаток", html)
        self.assertIn("Увеличьте предельное число банков", html)
        self.assertIn("«Без учёта лимита»", html)
        self.assertIn("что можно сделать:", text)

    def test_no_tips_without_remainder(self):
        html, text = self.tips(amount="100000")
        self.assertNotIn("Как уменьшить нераспределённый остаток", html)
        self.assertNotIn("что можно сделать:", text)

    def test_no_tips_for_discretisation_error(self):
        r = optimize([dep(1, 1, 12, 12, 1000, None)], params(1_001_000 - 1, InsuranceMode.NONE), CBR, 1_400_000)
        self.assertEqual(r.remainder_reasons[0], REASON_STEP)
        self.assertEqual(r.remainder_tips, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class ThousandsInNames(unittest.TestCase):
    """Тысячи в названиях вкладов пишутся как «тыс.», а не буквой «к»."""

    def test_display_name(self):
        from app.data_management.repository import display_name
        self.assertEqual(display_name("Новый старт (от 50к)"), "Новый старт (от 50 тыс.)")
        self.assertEqual(display_name("Россия (от 100 К)"), "Россия (от 100 тыс.)")
        self.assertEqual(display_name("Вклад (от 1.5млн)"), "Вклад (от 1.5млн)")
        self.assertEqual(display_name("Копилка"), "Копилка")
