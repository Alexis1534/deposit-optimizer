"""Приближённый метод — жадный алгоритм.

Вклады сортируются по абсолютному доходу за весь срок на рубль вложений
(коэффициент из подсистемы расчёта, а не процентная ставка), затем последовательно
наполняются с учётом остатка суммы, максимума вклада, лимита банка и числа банков K.
Работает на той же сетке, что и точный метод, — для корректного сравнения результатов.
"""
from dataclasses import dataclass
from typing import List, Tuple

from app.optimization.grid import Grid


@dataclass
class GreedySolution:
    value: float
    allocation: List[Tuple[object, int]]     # (GridItem, ячеек)
    banks_used: int


def solve(grid: Grid, K: int) -> GreedySolution:
    h = grid.step
    rest = grid.cells
    used_cells = {b.bank_id: 0 for b in grid.banks}
    used_with_interest = {b.bank_id: 0.0 for b in grid.banks}
    bank_of = {}
    for b in grid.banks:
        for it in b.items:
            bank_of[id(it)] = b
    items = sorted((it for b in grid.banks for it in b.items), key=lambda it: -it.coef)

    alloc, opened, value = [], set(), 0.0
    for it in items:
        if rest <= 0:
            break
        bank = bank_of[id(it)]
        if bank.bank_id not in opened and len(opened) >= K:
            continue
        cells = min(rest, it.hi, bank.cap_cells - used_cells[bank.bank_id])
        if bank.bound_rub is not None:
            room = bank.bound_rub - used_with_interest[bank.bank_id]
            cells = min(cells, int(room // (h * (1.0 + it.coef))))
        if cells < it.lo:
            continue
        alloc.append((it, cells))
        opened.add(bank.bank_id)
        used_cells[bank.bank_id] += cells
        used_with_interest[bank.bank_id] += cells * h * (1.0 + it.coef)
        rest -= cells
        value += it.coef * h * cells
    return GreedySolution(value, alloc, len(opened))
