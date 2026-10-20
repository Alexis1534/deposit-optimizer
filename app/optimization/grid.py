"""Дискретизация задачи распределения (п. 4.1.2.10 ТЗ).

Все суммы переводятся в «ячейки» размера h рублей:
  * нижняя граница вклада округляется вверх   (размещённая сумма не меньше минимума);
  * верхняя граница вклада округляется вниз    (не больше максимума);
  * лимит банка округляется вниз               (лимит не нарушается).
Отсюда погрешность не более одного шага на каждый использованный вклад.
"""
import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.models import Deposit, InsuranceMode

MIN_STEP = 1000


@dataclass
class GridItem:
    deposit: Deposit
    coef: float          # доход на 1 рубль за весь срок (из подсистемы расчёта)
    lo: int              # минимальное число ячеек
    hi: int              # максимальное число ячеек (с учётом лимита банка и M)


@dataclass
class GridBank:
    bank_id: int
    items: List[GridItem]
    cap_cells: int                   # предел вложенной суммы в банке, ячеек
    bound_rub: Optional[float]       # режим 3: предел «вложено + проценты», руб.; иначе None


@dataclass
class Grid:
    step: int
    cells: int                       # M — число ячеек эффективной суммы
    effective_amount: int
    banks: List[GridBank]            # только банки, в которых есть хотя бы один пригодный вклад
    mode: InsuranceMode


def base_step(deposits: List[Deposit]) -> int:
    """h = max(1000, 0.1 × наименьшая минимальная сумма среди отобранных вкладов)."""
    smallest_min = min(d.min_sum for d in deposits)
    return max(MIN_STEP, int(math.ceil(0.1 * smallest_min)))


def bank_principal_cap_rub(mode: InsuranceMode, insurance_limit: int,
                           personal_limit: Optional[int]) -> Optional[int]:
    """Предел вложенной суммы в один банк: меньшее из действующих ограничений (п. 4.1.2.8).

    Все банки справочника — участники ССВ (иначе запись отвергается при контроле
    целостности), поэтому страховой лимит действует на любой банк. В режиме 3 он
    ограничивает сумму вместе с процентами и учитывается отдельно (bound_rub),
    здесь остаётся только личное ограничение.
    """
    caps = []
    if personal_limit is not None:
        caps.append(personal_limit)
    if mode == InsuranceMode.PRINCIPAL:
        caps.append(insurance_limit)
    return min(caps) if caps else None


def effective_amount(amount: int, deposits: List[Deposit], mode: InsuranceMode,
                     insurance_limit: int) -> int:
    """S_эфф = min(S, Σ максимумов, лимит × число доступных банков)."""
    s = amount
    if all(d.max_sum is not None for d in deposits):
        s = min(s, sum(d.max_sum for d in deposits))
    if mode != InsuranceMode.NONE:
        s = min(s, insurance_limit * len({d.bank_id for d in deposits}))
    return s


def build_grid(deposits: List[Deposit], coefs: Dict[int, float], amount: int, step: int,
               mode: InsuranceMode, insurance_limit: int,
               personal_limit: Optional[int]) -> Grid:
    """Строит дискретную задачу с шагом step; вклады группируются по банкам."""
    s_eff = effective_amount(amount, deposits, mode, insurance_limit)
    M = s_eff // step
    grouped: "OrderedDict[int, List[Deposit]]" = OrderedDict()
    for d in sorted(deposits, key=lambda x: (x.bank_id, x.deposit_id)):
        grouped.setdefault(d.bank_id, []).append(d)

    banks = []
    for bank_id, deps in grouped.items():
        cap_rub = bank_principal_cap_rub(mode, insurance_limit, personal_limit)
        cap_cells = M if cap_rub is None else min(M, cap_rub // step)
        bound = float(insurance_limit) if mode == InsuranceMode.WITH_INTEREST else None
        if bound is not None:
            cap_cells = min(cap_cells, int(bound // step))
        items = []
        for d in deps:
            lo = max(1, -(-d.min_sum // step))                 # вверх
            hi = M if d.max_sum is None else d.max_sum // step  # вниз
            hi = min(hi, cap_cells)
            if lo <= hi:
                items.append(GridItem(d, coefs[d.deposit_id], lo, hi))
        if items:
            banks.append(GridBank(bank_id, items, cap_cells, bound))
    return Grid(step, M, s_eff, banks, mode)
