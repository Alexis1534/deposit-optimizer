"""Таблица 1 ТЗ — граничные тесты ввода."""
import unittest
from datetime import date

import helpers  # noqa: F401  (путь к проекту)
from app.ui.validation import FormState, validate

BANKS = [1, 2, 3]
TODAY = date(2026, 9, 16)


def run(**kw):
    base = dict(amount="100000", term_from="1", term_to="18", insurance_mode="2", other_income="0")
    base.update(kw)
    return validate(FormState(**base), BANKS, TODAY)


class InputBounds(unittest.TestCase):
    def test_01_amount_999(self):
        self.assertIn("меньше", run(amount="999").errors["amount"])

    def test_02_amount_1000(self):
        self.assertEqual(run(amount="1000").errors, {})

    def test_03_amount_100m(self):
        self.assertEqual(run(amount="100000000").errors, {})

    def test_04_amount_100m_plus_1(self):
        self.assertIn("превышает", run(amount="100000001").errors["amount"])

    def test_05_term_from_gt_to(self):
        self.assertIn("больше верхней", run(term_from="12", term_to="6").errors["term_from"])

    def test_06_term_0(self):
        self.assertIn("меньше", run(term_from="0").errors["term_from"])

    def test_07_term_18(self):
        self.assertEqual(run(term_from="18", term_to="18").errors, {})

    def test_08_term_19(self):
        self.assertIn("превышает", run(term_to="19").errors["term_to"])

    def test_09_all_banks_excluded(self):
        self.assertIn("исключены все банки", run(excluded=frozenset(BANKS)).errors["banks"])

    def test_10_k_zero(self):
        self.assertIn("меньше", run(max_banks="0").errors["max_banks"])

    def test_11_k_above_banks_clamped(self):
        r = run(max_banks="10", excluded=frozenset({3}))
        self.assertEqual(r.errors, {})
        self.assertEqual(r.params.max_banks, 2)
        self.assertTrue(r.notices)

    def test_12_personal_500(self):
        self.assertIn("меньше", run(personal_enabled=True, personal_limit="500").errors["personal_limit"])

    def test_extra_non_numeric_and_fraction(self):
        self.assertIn("целое", run(amount="12,5").errors["amount"])
        self.assertIn("целое", run(amount="abc").errors["amount"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
