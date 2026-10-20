"""Точный метод — динамическое программирование (п. 4.1.1.4 ТЗ).

Состояние (m, u, k): m — ячеек израсходовано всего, u — в текущем банке, k — банков
задействовано. Вклады обрабатываются сгруппированными по банкам; при переходе к банку
u обнуляется, k увеличивается (банк засчитывается только при u > 0).

Реализация состояния без явного перебора u:
  1. Внутри банка b строится функция f_b(u) — наибольший доход при размещении в банке
     ровно u ячеек («цепочка» по вкладам банка). Переход по вкладу i:
         F_i[u] = max(F_{i-1}[u],  c_i·h·u + max_{j ∈ [u-hi_i, u-lo_i]} (F_{i-1}[j] − c_i·h·j))
     — это и есть линейный переход через монотонную очередь (здесь — векторизованный
     скользящий максимум van Herk / Gil-Werman, O(длины) на вклад).
  2. f_b разбивается на участки, на которых она линейна. На каждом участке [a, b]
     со скатом s переход главной таблицы
         dp_k[m] = max(dp_k[m], max_{u ∈ [a,b]} dp_{k-1}[m-u] + f_b(u))
     сводится к тому же скользящему максимуму по m, O(M) на участок.
  Итого O(Σ_b участков_b · M · (K+1)) вместо квадратичного перебора сумм.

Режим 3 (лимит вместе с процентами): ограничение банка «u·h + доход ≤ лимит» касается
самого дохода. Для банка с небольшим числом вкладов область, где предел действует,
просчитывается перебором подмножеств вкладов. Для банка с бóльшим числом вкладов в ДП
берутся только u, при которых наибольший доход сам укладывается в предел, — такие значения
заведомо достижимы; затем нераспределённые ячейки докладываются в уже привлечённые банки
до предела (_top_up). Ограничения на число вкладов в банке нет.

Модуль не содержит правил начисления процентов: коэффициенты c_i получены заранее
из подсистемы расчёта доходности и лежат в GridItem.coef.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from app.optimization.grid import Grid, GridBank

NEG = -1e18
NEG_HALF = -5e17
TOL = 1e-6
# Режим 3: у банка с числом вкладов не больше EXACT_SUBSET_MAX область действия предела
# «вложено + проценты» просчитывается перебором подмножеств вкладов (2^n). Доход у предела
# при этом достигается с точностью до разброса ставок банка на один шаг — меньше дохода
# одного шага, в пределах погрешности п. 4.1.2.10. Порог ограничивает время перебора
# рамками п. 4.1.3.5 (2 с на расчёт).
EXACT_SUBSET_MAX = 6
SUBSET_OVERHEAD = 3000   # накладные расходы на одно подмножество в единицах объёма


# ----------------------------------------------------------- скользящий максимум
def sliding_max(z: np.ndarray, w: int) -> np.ndarray:
    """Максимум в окне ширины w по последней оси (алгоритм van Herk / Gil-Werman).

    Возвращает массив длины n − w + 1. Время O(n), без цикла Python по элементам.
    """
    if w == 1:
        return z
    n = z.shape[-1]
    pad = (-n) % w
    if pad:
        z = np.concatenate([z, np.full(z.shape[:-1] + (pad,), NEG)], axis=-1)
    blocks = z.reshape(z.shape[:-1] + (-1, w))
    prefix = np.maximum.accumulate(blocks, axis=-1).reshape(z.shape)
    suffix = np.maximum.accumulate(blocks[..., ::-1], axis=-1)[..., ::-1].reshape(z.shape)
    return np.maximum(suffix[..., : n - w + 1], prefix[..., w - 1: n])


def window_transition(src: np.ndarray, a: int, b: int, slope: float, fa: float,
                      idx: np.ndarray) -> np.ndarray:
    """cand[m] = max_{u∈[a,b], u≤m} src[m−u] + fa + slope·(u−a); для m < a — NEG."""
    n = src.shape[-1]
    cand = np.full(src.shape, NEG)
    if a > n - 1:
        return cand
    b = min(b, n - 1)
    w = b - a + 1
    y = src[..., : n - a] - slope * idx[: n - a]
    if w > 1:
        y = np.concatenate([np.full(y.shape[:-1] + (w - 1,), NEG), y], axis=-1)
    r = sliding_max(y, w)
    cand[..., a:] = r + slope * idx[a:] + (fa - slope * a)
    return cand


# ------------------------------------------------------------ функция банка
@dataclass
class BankFunction:
    bank: GridBank
    f: np.ndarray                          # f(u), u = 0..L; NEG — недостижимо
    chain: List[np.ndarray]                # слои цепочки F_0..F_n (для восстановления)
    subset_used: Optional[np.ndarray]      # режим 3, малый банк: маска подмножества для u
    runs: List[Tuple[int, int, float, float]]   # участки линейности (a, b, скат, f(a))
    h: int                                 # шаг сетки, руб.

    @property
    def L(self):
        return len(self.f) - 1


def _chain(bank: GridBank, L: int, h: int):
    idx = np.arange(L + 1, dtype=float)
    F = np.full(L + 1, NEG)
    F[0] = 0.0
    layers = [F]
    for it in bank.items:
        s = it.coef * h
        cand = window_transition(F, it.lo, min(it.hi, L), s, s * it.lo, idx)
        F = np.maximum(F, cand)
        layers.append(F)
    return F, layers


def _subset_curves(items, mask, L, h):
    """Для подмножества вкладов: диапазон u и доходы заполнения «от дорогих» и «от дешёвых»."""
    chosen = [it for k, it in enumerate(items) if mask >> k & 1]
    lo_s = sum(it.lo for it in chosen)
    hi_s = min(sum(it.hi for it in chosen), L)
    if lo_s > hi_s:
        return None
    us = np.arange(lo_s, hi_s + 1)
    extra = (us - lo_s).astype(float)
    base = sum(it.coef * h * it.lo for it in chosen)

    def fill(order):
        xs, ys = [0.0], [0.0]
        for it in order:
            xs.append(xs[-1] + (it.hi - it.lo))
            ys.append(ys[-1] + it.coef * h * (it.hi - it.lo))
        return base + np.interp(extra, xs, ys)

    high = fill(sorted(chosen, key=lambda it: -it.coef))
    low = fill(sorted(chosen, key=lambda it: it.coef))
    spread = (max(it.coef for it in chosen) - min(it.coef for it in chosen)) * h
    return us, high, low, spread


def _subset_function(bank: GridBank, L: int, h: int, binding: np.ndarray):
    """Точное значение f(u) в области действия предела — перебором подмножеств вкладов."""
    f3 = np.full(L + 1, NEG)
    used = np.zeros(L + 1, dtype=np.int64)
    for mask in range(1, 1 << len(bank.items)):
        cur = _subset_curves(bank.items, mask, L, h)
        if cur is None:
            continue
        us, high, low, spread = cur
        allowed = bank.bound_rub - us * h
        val = np.where(high <= allowed + TOL, high,
                       np.where(low <= allowed + TOL, np.maximum(low, allowed - spread), NEG))
        better = val > f3[us] + TOL
        f3[us[better]] = val[better]
        used[us[better]] = mask
    return f3, np.where(binding, used, 0)


def build_bank_function(bank: GridBank, M: int, h: int) -> BankFunction:
    L = min(M, bank.cap_cells, sum(it.hi for it in bank.items))
    f, layers = _chain(bank, L, h)
    subset_used = None

    if bank.bound_rub is not None:
        u = np.arange(L + 1, dtype=float)
        reachable = f > NEG_HALF
        allowed = bank.bound_rub - u * h           # предел дохода при u ячейках
        binding = reachable & (f > allowed + TOL)
        if binding.any():
            # Ограничение «вложено + проценты ≤ лимита» касается самого дохода: при u
            # ячейках доход не должен превышать allowed(u).
            if len(bank.items) <= EXACT_SUBSET_MAX:
                f3, subset_used = _subset_function(bank, L, h, binding)
                f = np.where(binding, f3, f)
            else:
                # большой банк: берутся только u, при которых наибольший доход сам
                # укладывается в предел, — такие значения заведомо достижимы
                f = np.where(binding, NEG, f)

    return BankFunction(bank, f, layers, subset_used, _runs(f), h)


def _runs(f: np.ndarray) -> List[Tuple[int, int, float, float]]:
    """Разбиение f(u), u ≥ 1, на участки линейности (векторно)."""
    L = len(f) - 1
    if L < 1:
        return []
    fin = f > NEG_HALF
    fin[0] = False                       # u = 0 — «банк не задействован», отдельный переход
    s = np.zeros(L + 1)
    ok = fin[1:] & fin[:-1]
    s[1:][ok] = f[1:][ok] - f[:-1][ok]
    start = np.zeros(L + 1, dtype=bool)
    u = np.arange(L + 1)
    prev_fin = np.concatenate([[False], fin[:-1]])
    prev2_fin = np.concatenate([[False, False], fin[:-2]]) if L >= 2 else np.zeros(L + 1, bool)
    prev_s = np.concatenate([[0.0], s[:-1]])
    slope_change = (u >= 3) & prev2_fin & (np.abs(s - prev_s) > TOL)
    start = fin & (~prev_fin | slope_change)
    points = np.nonzero(fin)[0]
    if len(points) == 0:
        return []
    gid = np.cumsum(start)[points]
    last = np.nonzero(np.diff(gid))[0]
    ends = np.concatenate([points[last], [points[-1]]])
    starts = points[np.concatenate([[0], last + 1])]
    runs = []
    for a, b in zip(starts.tolist(), ends.tolist()):
        slope = float(f[a + 1] - f[a]) if b > a else 0.0
        runs.append((a, b, slope, float(f[a])))
    return runs


def linearized(bf: BankFunction) -> np.ndarray:
    g = np.full(bf.L + 1, NEG)
    for a, b, slope, fa in bf.runs:
        g[a:b + 1] = fa + slope * np.arange(b - a + 1)
    return g


# ---------------------------------------------------------------- оценка объёма
def volume(bank_functions: List[BankFunction], M: int, K: int) -> Tuple[int, int]:
    """Оценка объёма вычислений (элементарных векторных операций) и памяти (байт)."""
    rows = K + 1 if K < len(bank_functions) else 1
    work = sum(len(bf.runs) for bf in bank_functions) * rows * (M + 1)
    memory = (len(bank_functions) + 1) * rows * (M + 1) * 8
    return work, memory


def bank_function_cost(grid: Grid) -> int:
    """Оценка объёма построения функций банков (цепочки и перебор подмножеств режима 3)."""
    cost = 0
    for b in grid.banks:
        L = min(grid.cells, b.cap_cells)
        cost += len(b.items) * (L + 1)
        if b.bound_rub is not None and len(b.items) <= EXACT_SUBSET_MAX:
            cost += (1 << len(b.items)) * (L + 1 + SUBSET_OVERHEAD)
    return cost


# --------------------------------------------------------------------- решение
@dataclass
class ExactSolution:
    value: float
    allocation: List[Tuple[object, int]]      # (GridItem, ячеек)
    banks_used: int


def solve(bank_functions: List[BankFunction], M: int, K: int) -> ExactSolution:
    """Главная таблица ДП по банкам с учётом предельного числа банков K."""
    B = len(bank_functions)
    use_k = K < B                    # иначе ограничение по числу банков не действует
    rows = K + 1 if use_k else 1
    idx = np.arange(M + 1, dtype=float)
    dp = np.full((rows, M + 1), NEG)
    dp[0, 0] = 0.0
    layers = [dp]
    lin = []
    for bf in bank_functions:
        g = linearized(bf)
        lin.append(g)
        src = dp[:-1] if use_k else dp
        best = np.full(src.shape, NEG)
        for a, b, slope, fa in bf.runs:
            np.maximum(best, window_transition(src, a, b, slope, fa, idx), out=best)
        new = dp.copy()
        if use_k:
            np.maximum(new[1:], best, out=new[1:])
        else:
            np.maximum(new, best, out=new)
        new[new < NEG_HALF] = NEG
        layers.append(new)
        dp = new

    k_best, m_best = np.unravel_index(int(np.argmax(dp)), dp.shape)
    value = float(dp[k_best, m_best])

    # ---- восстановление решения: банк за банком в обратном порядке
    alloc, banks_used = [], 0
    k, m = int(k_best), int(m_best)
    for b in range(B - 1, -1, -1):
        prev, bf, g = layers[b], bank_functions[b], lin[b]
        skip = prev[k, m]
        if use_k and k == 0:
            continue
        src_row = prev[k - 1] if use_k else prev[k]
        umax = min(bf.L, m)
        if umax < 1:
            continue
        us = np.arange(1, umax + 1)
        take_vals = src_row[m - us] + g[us]
        j = int(np.argmax(take_vals))
        if take_vals[j] > skip + TOL:
            u = int(us[j])
            alloc.extend(_split_bank(bf, u))
            banks_used += 1
            m -= u
            if use_k:
                k -= 1
    alloc = _top_up(bank_functions, alloc, M)
    value = sum(it.coef * bf.h * c for bf in bank_functions for it, c in alloc if it in bf.bank.items)
    return ExactSolution(value, alloc, banks_used)


def _top_up(bank_functions: List[BankFunction], alloc: List[Tuple[object, int]], M: int):
    """Режим 3: докладывание нераспределённых ячеек в уже привлечённые банки до предела.

    Для банков с большим числом вкладов ДП берёт только значения, где наибольший доход
    сам укладывается в предел «вложено + проценты», поэтому у предела может остаться запас.
    Нераспределённые ячейки докладываются в вклады этих банков, начиная с наиболее
    доходного, пока запас позволяет. Каждое добавление увеличивает доход и не нарушает
    ни одного ограничения; новые банки не привлекаются (число банков не меняется).
    """
    cells = {id(it): c for it, c in alloc}
    by_id = {id(it): it for it, _ in alloc}
    free = M - sum(cells.values())
    for bf in bank_functions:
        bank = bf.bank
        if bank.bound_rub is None or free <= 0:
            continue
        mine = [it for it in bank.items if cells.get(id(it), 0) > 0]
        if not mine:
            continue
        h = bf.h
        used = sum(cells[id(it)] for it in mine)
        with_interest = sum(cells[id(it)] * h * (1 + it.coef) for it in mine)
        for it in sorted(bank.items, key=lambda x: -x.coef):
            if free <= 0:
                break
            have = cells.get(id(it), 0)
            room = min(it.hi - have, bank.cap_cells - used, free,
                       int((bank.bound_rub - with_interest + TOL) // (h * (1 + it.coef))))
            if have == 0 and room < it.lo:
                continue                      # новый вклад открывается только с минимума
            if room <= 0:
                continue
            cells[id(it)] = have + room
            by_id[id(it)] = it
            used += room
            free -= room
            with_interest += room * h * (1 + it.coef)
    return [(by_id[i], c) for i, c in cells.items() if c > 0]


def _split_bank(bf: BankFunction, u: int) -> List[Tuple[object, int]]:
    """Распределение u ячеек между вкладами банка."""
    items = bf.bank.items
    if bf.subset_used is not None and bf.subset_used[u]:
        return _split_subset(bf, u, int(bf.subset_used[u]))
    result = []
    for i in range(len(items), 0, -1):
        it, prev = items[i - 1], bf.chain[i - 1]
        skip = prev[u]
        hi = min(it.hi, u)
        take_best, a_best = NEG, 0
        if it.lo <= hi:
            a = np.arange(it.lo, hi + 1)
            vals = prev[u - a] + it.coef * bf.h * a
            j = int(np.argmax(vals))
            take_best, a_best = float(vals[j]), int(a[j])
        if take_best > skip + TOL:
            result.append((it, a_best))
            u -= a_best
    return result


def _split_subset(bf: BankFunction, u: int, mask: int) -> List[Tuple[object, int]]:
    """Режим 3, малый банк: распределение подмножества, доведённое до предела банка."""
    h = bf.h
    chosen = [it for k, it in enumerate(bf.bank.items) if mask >> k & 1]
    alloc = {id(it): it.lo for it in chosen}
    rest = u - sum(it.lo for it in chosen)
    for it in sorted(chosen, key=lambda x: x.coef):        # сначала наименее доходные
        add = min(rest, it.hi - it.lo)
        alloc[id(it)] += add
        rest -= add
    income = sum(it.coef * h * alloc[id(it)] for it in chosen)
    limit = bf.bank.bound_rub - u * h
    asc = sorted(chosen, key=lambda x: x.coef)
    for hi_it in reversed(asc):                            # перенос к более доходным вкладам
        for lo_it in asc:
            if lo_it.coef >= hi_it.coef:
                break
            gain = (hi_it.coef - lo_it.coef) * h
            movable = min(alloc[id(lo_it)] - lo_it.lo, hi_it.hi - alloc[id(hi_it)],
                          int((limit - income + TOL) // gain))
            if movable > 0:
                alloc[id(lo_it)] -= movable
                alloc[id(hi_it)] += movable
                income += movable * gain
    return [(it, alloc[id(it)]) for it in chosen if alloc[id(it)] > 0]
