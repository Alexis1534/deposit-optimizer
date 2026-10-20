"""Общие средства тестов: построение вкладов, независимый полный перебор."""
import itertools
import os
import random
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.models import CalcParams, CbrInfo, Deposit, InsuranceMode  # noqa: E402

CALC_DATE = date(2026, 9, 16)
CBR = CbrInfo(14.0, "15.09.2026", {2026: 16.0}, 12.948, "I.09.2026", from_cache=True)


def demo_db():
    """Временная копия демонстрационного справочника (8 банков, 19 вкладов).

    Тесты не опираются на database/deposits.db: в поставке он может быть заменён
    большим справочником, а проверкам нужны заранее известные записи.
    """
    import tempfile
    global _DEMO_DB
    if _DEMO_DB is None:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import init_db
        _DEMO_DB = os.path.join(tempfile.mkdtemp(), "demo.db")
        init_db.build(_DEMO_DB)
    return _DEMO_DB


_DEMO_DB = None


def dep(i, bank, rate, term, mn, mx, cap=False, name=None):
    return Deposit(i, bank, f"Банк {bank}", str(bank), name or f"Вклад {i}",
                   rate, term, mn, mx, cap, date(2026, 9, 1))


def params(amount, mode=InsuranceMode.PRINCIPAL, K=99, personal=None, other=0, t=(1, 18)):
    return CalcParams(amount, t[0], t[1], frozenset(), K, mode, personal, other, CALC_DATE)


def brute_force(grid, K):
    """Полный перебор всех вариантов на той же сетке: каждый вклад — 0 или lo..hi ячеек."""
    items = [(b, it) for b in grid.banks for it in b.items]
    h, best = grid.step, 0.0
    for combo in itertools.product(*[[0] + list(range(it.lo, it.hi + 1)) for _, it in items]):
        if sum(combo) > grid.cells:
            continue
        per, inc = {}, {}
        for (b, it), c in zip(items, combo):
            if c:
                per[b.bank_id] = per.get(b.bank_id, 0) + c
                inc[b.bank_id] = inc.get(b.bank_id, 0.0) + it.coef * h * c
        if len(per) > K:
            continue
        if any(per.get(b.bank_id, 0) > b.cap_cells or
               (b.bound_rub is not None and per.get(b.bank_id, 0) * h + inc.get(b.bank_id, 0) > b.bound_rub + 1e-6)
               for b in grid.banks):
            continue
        best = max(best, sum(inc.values()))
    return best


def synthetic(n, banks, seed=1):
    """Синтетический справочник для оценки производительности."""
    r = random.Random(seed)
    out = []
    for i in range(n):
        b = i % banks + 1
        mn = r.choice([10000, 30000, 50000, 100000, 300000])
        out.append(dep(i + 1, b, round(r.uniform(9, 17), 2),
                       r.choice([1, 2, 3, 4, 6, 9, 12, 13, 15, 18]), mn,
                       r.choice([None, None, mn * 10, mn * 50]), cap=r.random() < 0.4))
    return out
