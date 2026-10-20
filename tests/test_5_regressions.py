"""Регрессионные тесты: случаи, найденные при ручном тестировании."""
import random
import unittest
from datetime import date
from decimal import Decimal

from helpers import CBR, ROOT, demo_db, dep, params
from app.models import InsuranceMode
from app.optimization.service import optimize
from app.ui.routes import create_app
from app.ui.validation import FormState, validate
from app.yield_calc import calculator as calc

TODAY = date(2026, 9, 16)


class LimitsLongTerms(unittest.TestCase):
    """Лимиты при сроках больше года (банки — участники ССВ)."""

    def test_limits_hold_for_terms_13_18(self):
        rnd = random.Random(5)
        for trial in range(120):
            nb = rnd.randint(2, 10)
            deps = []
            for i in range(rnd.randint(nb, 30)):
                mn = rnd.choice([1000, 10000, 50000, 300000])
                deps.append(dep(i + 1, rnd.randint(1, nb), rnd.uniform(8, 25), rnd.randint(13, 18), mn,
                                rnd.choice([None, mn * 20]), cap=rnd.random() < .5))
            mode = rnd.choice([InsuranceMode.PRINCIPAL, InsuranceMode.WITH_INTEREST])
            personal = rnd.choice([None, 700000])
            r = optimize(deps, params(rnd.choice([3_000_000, 20_000_000, 100_000_000]), mode, personal=personal),
                         CBR, 1_400_000)
            limit = min(1_400_000, personal or 10 ** 12)
            for res in (r.exact, r.approx):
                per, with_int = {}, {}
                for p in res.positions:
                    per[p.deposit.bank_id] = per.get(p.deposit.bank_id, 0) + p.amount
                    with_int[p.deposit.bank_id] = with_int.get(p.deposit.bank_id, 0) + p.amount + float(p.income)
                for b in per:
                    with self.subTest(trial=trial, bank=b):
                        self.assertLessEqual(per[b], limit)
                        if mode == InsuranceMode.WITH_INTEREST:
                            self.assertLessEqual(with_int[b], 1_400_000 + 0.01)


class BankLicenseIntegrity(unittest.TestCase):
    """п. 4.2.3: заполненность и уникальность номера лицензии банка."""

    def catalog_with(self, sql):
        import sqlite3, tempfile, os as _os, sys as _sys
        _sys.path.insert(0, _os.path.join(ROOT, "tools"))
        import init_db
        from app.data_management.repository import load_catalog
        path = _os.path.join(tempfile.mkdtemp(), "t.db")
        init_db.build(path)
        con = sqlite3.connect(path)
        con.executescript(sql)
        con.commit(); con.close()
        return load_catalog(path)

    def test_empty_license(self):
        cat = self.catalog_with("UPDATE bank SET license_no = '' WHERE bank_id = 1;")
        self.assertTrue(any("не заполнен номер лицензии" in i.problem for i in cat.issues))
        self.assertFalse([d for d in cat.deposits if d.bank_id == 1])

    def test_duplicate_license_rejects_both_banks(self):
        cat = self.catalog_with("UPDATE bank SET license_no = 'Д-0001' WHERE bank_id = 2;")
        self.assertEqual(sum("совпадает с номером банка" in i.problem for i in cat.issues), 2)
        self.assertFalse([d for d in cat.deposits if d.bank_id in (1, 2)])
        self.assertTrue([d for d in cat.deposits if d.bank_id == 3])

    def test_no_dis_member_column(self):
        """Признака участия в ССВ в записи о банке нет: справочник содержит только участников."""
        import sqlite3
        con = sqlite3.connect(demo_db())
        cols = [r[1] for r in con.execute("PRAGMA table_info(bank)")]
        con.close()
        self.assertEqual(cols, ["bank_id", "name", "license_no"])


class OtherIncome(unittest.TestCase):
    """Доход из других источников с копейками."""

    def form(self, v):
        return validate(FormState(amount="100000", term_from="1", term_to="18", insurance_mode="2",
                                  other_income=v), [1], TODAY)

    def test_accepted_formats(self):
        for v, expected in [("1500,50", "1500.50"), ("1500.5", "1500.5"), ("1 500", "1500"), ("", "0")]:
            with self.subTest(v=v):
                r = self.form(v)
                self.assertEqual(r.errors, {})
                self.assertEqual(r.params.other_income, Decimal(expected))

    def test_rejected(self):
        for v in ["-100", "12,345", "abc", "1000000000,01"]:
            with self.subTest(v=v):
                self.assertIn("other_income", self.form(v).errors)

    def test_full_calculation_with_kopecks(self):
        app = create_app(demo_db(), online=False, today_fn=lambda: TODAY)
        c = app.test_client()
        r = c.post("/calculate", data={"amount": "2000000", "term_from": "1", "term_to": "18",
                                       "insurance_mode": "2", "other_income": "175 000,55"})
        html = r.data.decode()
        self.assertEqual(r.status_code, 200)
        self.assertIn("Доход до налогообложения", html)
        self.assertIn("175\u00a0000,55", html)
        self.assertEqual(c.get("/save").status_code, 200)


class CurrentYearTax(unittest.TestCase):
    """Строка текущего года в таблице НДФЛ показывается всегда."""

    def test_current_year_row_present(self):
        d = dep(1, 1, 20, 12, 1, None)
        pos = calc.build_positions([(d, 1_000_000)], calc.coefficients([d], TODAY), TODAY)
        years, _ = calc.compute_tax(pos, 0, TODAY, 14.0, {2026: 16.0})
        self.assertEqual([t.year for t in years], [2026, 2027])
        self.assertEqual(years[0].tax, Decimal("0.00"))


class StaleSave(unittest.TestCase):
    def test_no_save_after_failed_calculation(self):
        app = create_app(demo_db(), online=False, today_fn=lambda: TODAY)
        c = app.test_client()
        base = {"amount": "2000000", "term_from": "1", "term_to": "18", "insurance_mode": "2", "other_income": "0"}
        c.post("/calculate", data=base)
        self.assertIn("attachment", c.get("/save").headers.get("Content-Disposition", ""))
        c.post("/calculate", data=dict(base, amount="999"))
        self.assertNotIn("attachment", c.get("/save").headers.get("Content-Disposition", ""))


class KeyRateOnFirstDays(unittest.TestCase):
    """Сервис ЦБ отдаёт ставку только за рабочие дни; 1 января и другие первые числа,
    выпавшие на выходные, в ответе отсутствуют. Ставка на такое число — последнее значение до него.
    В 2026 году 16 % действовали с 21.12.2025 по 15.02.2026 → необлагаемая сумма 160 000 р."""

    XML = "".join(f"<KR><DT>{d}T00:00:00+03:00</DT><Rate>{r}</Rate></KR>" for d, r in [
        ("2025-12-19", "16.50"), ("2025-12-22", "16.00"), ("2026-01-12", "16.00"),
        ("2026-02-02", "16.00"), ("2026-02-16", "15.50"), ("2026-03-23", "15.00"),
        ("2026-04-27", "14.50"), ("2026-06-22", "14.25"), ("2026-07-27", "14.00"),
        ("2026-10-14", "14.00")])

    def test_year_max_uses_rate_in_force_on_first_day(self):
        from app.external_sources import cbr_client
        points = cbr_client.parse_key_rates(self.XML)
        self.assertEqual(cbr_client.year_max_on_month_starts(points, date(2026, 10, 18)), 16.0)
        # декабрьские 16,5 % относятся к прошлому году и в максимум 2026 года не входят
        self.assertEqual(cbr_client.year_max_on_month_starts(points, date(2026, 1, 5)), 16.0)

    def test_fetch_requests_previous_december(self):
        from app.external_sources import cbr_client
        sent = {}
        original = cbr_client._http
        cbr_client._http = lambda url, data=None, headers=None: (sent.setdefault("body", data), self.XML)[1]
        try:
            current, as_of, year_max = cbr_client.fetch_key_rates(date(2026, 10, 18))
        finally:
            cbr_client._http = original
        self.assertIn(b"2025-12-01", sent["body"])
        self.assertEqual((current, as_of, year_max), (14.0, "14.10.2026", 16.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class InsuredWithInterest(unittest.TestCase):
    """Проценты в пределах лимита тоже застрахованы."""

    def test_partially_filled_bank_interest_insured(self):
        d = dep(1, 1, 20, 12, 1000, None)
        pos = calc.build_positions([(d, 1_000_000)], calc.coefficients([d], TODAY), TODAY)
        insured, uninsured = calc.insured_split(pos, 1_400_000)
        self.assertEqual(insured, Decimal("1000000") + pos[0].income)   # 1,2 млн < лимита
        self.assertEqual(uninsured, Decimal("0"))

    def test_interest_above_limit_not_insured(self):
        d = dep(1, 1, 20, 12, 1000, None)
        pos = calc.build_positions([(d, 1_400_000)], calc.coefficients([d], TODAY), TODAY)
        insured, uninsured = calc.insured_split(pos, 1_400_000)
        self.assertEqual(insured, Decimal("1400000"))
        self.assertEqual(uninsured, pos[0].income)

    def test_mode_3_fully_insured(self):
        deps = [dep(i, i, 15, 12, 1000, None) for i in range(1, 6)]
        r = optimize(deps, params(6_000_000, InsuranceMode.WITH_INTEREST), CBR, 1_400_000)
        self.assertEqual(r.uninsured, Decimal("0"))
        self.assertEqual(r.insured, Decimal(r.exact.placed) + r.exact.income)


class ProgressiveTax(unittest.TestCase):
    """13 % и 15 % с части базы свыше 2,4 млн р."""

    def make(self, income):
        d = dep(1, 1, 10, 12, 1, None)
        coef = calc.income_coefficient(d, TODAY)
        return calc.build_positions([(d, round(income / coef))], {1: coef}, TODAY)

    def test_below_threshold_13(self):
        years, total = calc.compute_tax(self.make(1_000_000), 0, TODAY, 14.0, {2026: 16.0})
        y = [t for t in years if t.year == 2027][0]
        self.assertEqual(y.scale, "13 %")
        self.assertEqual(y.tax_full, calc.round_money(y.base * Decimal("0.13")))

    def test_above_threshold_13_and_15(self):
        years, total = calc.compute_tax(self.make(4_000_000), 0, TODAY, 14.0, {2026: 16.0})
        y = [t for t in years if t.year == 2027][0]
        self.assertGreater(y.base, Decimal("2400000"))
        self.assertEqual(y.scale, "13 % / 15 %")
        expected = calc.round_money(Decimal("2400000") * Decimal("0.13")
                                    + (y.base - Decimal("2400000")) * Decimal("0.15"))
        self.assertEqual(y.tax_full, expected)
        self.assertEqual(y.high_part, y.base - Decimal("2400000"))


class RemainderReasons(unittest.TestCase):
    """Перечень причин остатка с иерархией (п. 4.1.3.4)."""

    def test_max_banks_and_bank_limit(self):
        deps = [dep(i, i, 12, 12, 1000, None) for i in range(1, 6)]
        r = optimize(deps, params(10_000_000, InsuranceMode.PRINCIPAL, K=3), CBR, 1_400_000)
        from app.optimization.service import REASON_BANK_LIMIT, REASON_MAX_BANKS
        self.assertEqual(r.remainder_reasons[0], REASON_MAX_BANKS)
        self.assertIn(REASON_BANK_LIMIT, r.remainder_reasons)

    def test_all_deposits_at_maximum(self):
        deps = [dep(1, 1, 12, 12, 1000, 100_000), dep(2, 2, 11, 12, 1000, 100_000)]
        r = optimize(deps, params(500_000, InsuranceMode.NONE), CBR, 1_400_000)
        from app.optimization.service import REASON_MAX_DEPOSITS
        self.assertEqual(r.remainder, 300_000)
        self.assertEqual(r.remainder_reasons[0], REASON_MAX_DEPOSITS)

    def test_small_remainder_is_discretisation(self):
        deps = [dep(1, 1, 12, 12, 1000, None)]
        r = optimize(deps, params(1_000_500, InsuranceMode.NONE), CBR, 1_400_000)
        from app.optimization.service import REASON_STEP
        self.assertEqual(r.remainder, 500)
        self.assertEqual(r.remainder_reasons[0], REASON_STEP)

    def test_no_reason_when_fully_placed(self):
        deps = [dep(1, 1, 12, 12, 1000, None)]
        r = optimize(deps, params(1_000_000, InsuranceMode.NONE), CBR, 1_400_000)
        self.assertEqual(r.remainder_reasons, [])


class SuspiciousInResult(unittest.TestCase):
    """п. 4.1.3.2: предупреждение, если в распределение вошёл вклад с нетипично высокой ставкой."""

    def test_warning_shown(self):
        app = create_app(demo_db(), online=False, today_fn=lambda: TODAY)
        c = app.test_client()
        html = c.post("/calculate", data={"amount": "1000000", "term_from": "12", "term_to": "12",
                                          "insurance_mode": "2", "other_income": "0"}).data.decode()
        self.assertIn("с нетипично высокой ставкой", html)          # «Меридиан рекорд», 17,50 %
        self.assertIn("Меридиан рекорд", html)
        self.assertIn("нетипично высокая ставка, сверьте условия", c.get("/save").data.decode())

    def test_no_warning_for_plain_rates(self):
        app = create_app(demo_db(), online=False, today_fn=lambda: TODAY)
        c = app.test_client()
        html = c.post("/calculate", data={"amount": "1000000", "term_from": "18", "term_to": "18",
                                          "insurance_mode": "2", "other_income": "0"}).data.decode()
        self.assertNotIn("с нетипично высокой ставкой", html)


class KeyRateYearMax(unittest.TestCase):
    """Необлагаемая сумма считается по ставкам на 1-е число месяца (п. 1 ст. 214.2 НК РФ)."""

    def test_peak_inside_month_is_ignored(self):
        import urllib.request
        from app.external_sources import cbr_client
        xml = ("<KR><DT>2026-01-01T00:00:00</DT><Rate>16.00</Rate></KR>"
               "<KR><DT>2026-01-20T00:00:00</DT><Rate>21.00</Rate></KR>"   # пик внутри месяца
               "<KR><DT>2026-02-01T00:00:00</DT><Rate>15.00</Rate></KR>"
               "<KR><DT>2026-03-01T00:00:00</DT><Rate>14.00</Rate></KR>")

        class Fake:
            def read(self): return xml.encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        original = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: Fake()
        try:
            rate, as_of, year_max = cbr_client.fetch_key_rates(date(2026, 3, 10))
        finally:
            urllib.request.urlopen = original
        self.assertEqual(year_max, 16.00)      # не 21.00
        self.assertEqual(rate, 14.00)
        self.assertEqual(as_of, "01.03.2026")


class NonTaxableCarryover(unittest.TestCase):
    """п. 4.1.2.6: накопление неиспользованных необлагаемых сумм всех лет срока вклада."""

    def tax(self, term, cap=False, amount=3_000_000, other=0, deposits=None, today=TODAY):
        deps = deposits or [dep(1, 1, 12, term, 1, None, cap=cap)]
        coefs = calc.coefficients(deps, today)
        pos = calc.build_positions([(d, amount) for d in deps], coefs, today)
        return pos, calc.compute_tax(pos, other, today, 14.0, {2026: 16.0})

    def test_all_years_of_term_accumulate(self):
        """Вклад на 18 мес.: переносятся остатки и 2026, и 2027 годов."""
        pos, (years, _) = self.tax(18)
        y = {t.year: t for t in years}
        caps = calc.carryover_caps(pos[0].deposit, pos[0].income, TODAY)
        self.assertEqual(sorted(caps), [2026, 2027])
        expected = min(Decimal("160000.00"), caps[2026]) + min(Decimal("140000.00"), caps[2027])
        self.assertEqual(y[2028].carried, expected)
        self.assertEqual(y[2028].base, pos[0].income - Decimal("140000.00") - expected)

    def test_source_year_limited_by_day_share(self):
        """Из каждого года берётся не больше дохода, приходящегося на него по дням."""
        pos, (years, _) = self.tax(16, amount=100_000)
        caps = calc.carryover_caps(pos[0].deposit, pos[0].income, TODAY)
        self.assertLess(sum(caps.values()), Decimal("300000"))
        self.assertEqual({t.year: t for t in years}[2028].carried, sum(caps.values()))

    def test_no_carryover_with_capitalization(self):
        _, (years, _) = self.tax(18, cap=True)
        self.assertTrue(all(t.carried == Decimal("0") for t in years))

    def test_no_carryover_for_short_term(self):
        for term in (12, 15):
            with self.subTest(term=term):
                _, (years, _) = self.tax(term)
                self.assertTrue(all(t.carried == Decimal("0") for t in years))

    def test_income_in_source_year_reduces_pool(self):
        long_dep = dep(1, 1, 12, 18, 1, None)          # завершается в 2028
        short_dep = dep(2, 2, 12, 12, 1, None)         # доход в 2027 «съедает» остаток года
        pos, (years, _) = self.tax(0, deposits=[long_dep, short_dep], amount=1_000_000)
        y = {t.year: t for t in years}
        caps = calc.carryover_caps(long_dep, [p for p in pos if p.deposit is long_dep][0].income, TODAY)
        left_2027 = max(Decimal("140000.00") - y[2027].deposit_income, Decimal("0"))
        expected = min(Decimal("160000.00"), caps[2026]) + min(left_2027, caps[2027])
        self.assertEqual(y[2028].carried, expected)

    def test_two_deposits_share_the_same_pool(self):
        """Два вклада с завершением в одном году делят один и тот же остаток каждого года."""
        deps = [dep(1, 1, 12, 18, 1, None), dep(2, 2, 11, 18, 1, None)]
        _, (years, _) = self.tax(0, deposits=deps, amount=3_000_000)
        self.assertEqual({t.year: t for t in years}[2028].carried,
                         Decimal("160000.00") + Decimal("140000.00"))

    def test_earlier_end_year_uses_pool_first(self):
        """Общий источник расходуется по хронологии: сначала более ранний год завершения."""
        today = date(2026, 8, 15)
        first_dep = dep(1, 1, 12, 16, 1, None)         # завершение 15.12.2027
        later_dep = dep(2, 2, 12, 18, 1, None)         # завершение 15.02.2028
        pos, (years, _) = self.tax(0, deposits=[first_dep, later_dep], amount=3_000_000, today=today)
        y = {t.year: t for t in years}
        income = {p.deposit.deposit_id: p.income for p in pos}
        self.assertEqual({p.end_date.year for p in pos}, {2027, 2028})

        caps_first = calc.carryover_caps(first_dep, income[1], today)
        caps_later = calc.carryover_caps(later_dep, income[2], today)
        # 2027 год завершения берёт из остатка 2026 года первым
        taken_2026 = min(Decimal("160000.00"), caps_first[2026])
        self.assertEqual(y[2027].carried, taken_2026)
        # 2028 году достаётся остаток 2026 года и неиспользованная часть 2027 года
        left_2026 = Decimal("160000.00") - taken_2026
        left_2027 = max(y[2027].non_taxable - y[2027].deposit_income, Decimal("0"))
        self.assertEqual(y[2028].carried,
                         min(left_2026, caps_later[2026]) + min(left_2027, caps_later[2027]))

    def test_carryover_reduces_tax(self):
        _, (_, with_rule) = self.tax(18)
        original = calc.CARRYOVER_MIN_TERM
        calc.CARRYOVER_MIN_TERM = 99
        try:
            _, (_, without_rule) = self.tax(18)
        finally:
            calc.CARRYOVER_MIN_TERM = original
        self.assertLess(with_rule, without_rule)
