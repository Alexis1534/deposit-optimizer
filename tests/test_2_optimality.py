"""Таблица 2 ТЗ — проверка оптимальности точного метода (+ расширенная случайная сверка)."""
import random
import unittest

from helpers import CBR, brute_force, dep, params
from app.models import InsuranceMode
from app.optimization import exact_dp
from app.optimization.grid import base_step, build_grid
from app.optimization.service import optimize
from app.yield_calc import calculator as calc


def exact_on_grid(deposits, p, limit=1_400_000):
    coefs = calc.coefficients(deposits, p.calc_date)
    g = build_grid(deposits, coefs, p.amount, base_step(deposits), p.insurance_mode, limit, p.personal_limit)
    bfs = [bf for bf in (exact_dp.build_bank_function(b, g.cells, g.step) for b in g.banks) if bf.runs]
    k = min(p.max_banks, len(bfs))
    sol = exact_dp.solve(bfs, g.cells, k)
    value = sum(it.coef * g.step * c for it, c in sol.allocation)
    return g, k, value


class Optimality(unittest.TestCase):
    def test_1_five_deposits_limit_off(self):
        deps = [dep(1, 1, 18, 12, 3000, 6000), dep(2, 1, 16, 6, 2000, None, cap=True),
                dep(3, 2, 17, 12, 5000, 9000), dep(4, 3, 12, 18, 1000, None),
                dep(5, 3, 21, 3, 7000, 7000)]
        p = params(15000, InsuranceMode.NONE, K=2)
        g, k, value = exact_on_grid(deps, p)
        self.assertAlmostEqual(value, brute_force(g, k), places=4)

    def test_2_six_deposits_principal_limit(self):
        deps = [dep(1, 1, 20, 12, 2000, 5000), dep(2, 1, 15, 12, 1000, None),
                dep(3, 2, 19, 6, 4000, 4000), dep(4, 2, 14, 18, 1000, 8000, cap=True),
                dep(5, 3, 11, 12, 3000, None), dep(6, 3, 18, 9, 6000, 12000)]
        p = params(20000, InsuranceMode.PRINCIPAL)
        g, k, value = exact_on_grid(deps, p, limit=7000)   # малый лимит, чтобы он действовал
        self.assertTrue(any(b.cap_cells < g.cells for b in g.banks))
        self.assertAlmostEqual(value, brute_force(g, k), places=4)

    def test_3_greedy_counterexample(self):
        deps = [dep(1, 1, 20, 12, 60000, 60000, name="А"), dep(2, 2, 19, 12, 50000, None, name="Б"),
                dep(3, 3, 5, 12, 1000, None, name="В")]
        for mode in InsuranceMode:
            with self.subTest(mode=mode.name):
                r = optimize(deps, params(100000, mode), CBR, 1_400_000)
                self.assertEqual(str(r.exact.income), "19000.00")
                self.assertEqual(str(r.approx.income), "14000.00")
                self.assertEqual([(x.deposit.name, x.amount) for x in r.exact.positions], [("Б", 100000)])

    def test_extra_random_vs_brute_force(self):
        rnd = random.Random(2026)
        for trial in range(250):
            deps, i = [], 0
            for b in range(1, rnd.randint(1, 3) + 1):
                for _ in range(rnd.randint(1, 3)):
                    i += 1
                    mn = rnd.choice([1000, 2000, 3000, 5000])
                    deps.append(dep(i, b, rnd.choice([5, 10, 15, 20, 30, 45]), rnd.choice([3, 6, 12, 18]),
                                    mn, rnd.choice([None, mn + rnd.randint(0, 6) * 1000]),
                                    cap=rnd.random() < .5))
            deps = deps[:6]
            mode = rnd.choice(list(InsuranceMode))
            p = params(rnd.choice([8000, 12000, 15000]), mode, K=rnd.randint(1, 3),
                       personal=rnd.choice([None, None, 5000, 7000]))
            g, k, value = exact_on_grid(deps, p, limit=rnd.choice([3500, 4700, 6000, 9000]))
            best = brute_force(g, k)
            with self.subTest(trial=trial, mode=mode.name):
                if mode != InsuranceMode.WITH_INTEREST:
                    self.assertAlmostEqual(value, best, places=4)       # режимы 1 и 2 — точно
                else:
                    # режим 3: решение допустимо и уступает оптимуму не более чем на
                    # разброс ставок банка на один шаг (см. exact_dp.EXACT_SUBSET_MAX)
                    bound = sum((max(it.coef for it in b.items) - min(it.coef for it in b.items))
                                * g.step for b in g.banks)
                    self.assertLessEqual(value, best + 1e-4)
                    self.assertLessEqual(best - value, bound + 1e-4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
