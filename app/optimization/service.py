"""Подсистема оптимизации распределения — точка входа (п. 4.1.1.4 ТЗ).

Порядок расчёта:
  1. коэффициенты доходности всех отобранных вкладов — вызов подсистемы расчёта (один раз);
  2. шаг дискретизации и оценка объёма; при превышении — укрупнение шага (п. 4.1.2.11);
  3. точный метод (ДП) и контроль его результата по приближённому (жадному) методу;
  4. выбранное распределение → подсистема расчёта: доходы позиций, НДФЛ, застрахованная часть;
  5. доход в один вклад, прирост при K+1, причина остатка.
Все данные приходят параметрами; модуль не хранит состояния.
"""
import math
from decimal import Decimal
from typing import Dict, List

from app.models import (CalcParams, CalcResult, CbrInfo, Deposit, InsuranceMode,
                        MethodResult)
from app.optimization import exact_dp, greedy
from app.optimization.grid import Grid, bank_principal_cap_rub, base_step, build_grid
from app.yield_calc import calculator as calc

# Допустимый объём вычислений в условных единицах (обработка одного элемента массива),
# при котором расчёт укладывается в 2 с (п. 4.1.3.5). Построение функций банков на единицу
# объёма примерно вдвое дороже главной таблицы ДП и учитывается с весом BANK_FUNCTION_WEIGHT.
WORK_BUDGET = 60_000_000
BANK_FUNCTION_WEIGHT = 2
RUNS_PER_DEPOSIT = 1.5
MEMORY_BUDGET = 256 * 1024 * 1024

# Причины нераспределённого остатка в порядке старшинства: первая подходящая
# показывается как основная, остальные — как сопутствующие (п. 4.1.3.4).
REASON_STEP = ("величина остатка не превышает шаг дискретизации, применённый при решении "
               "задачи распределения (погрешность дискретизации)")
REASON_MAX_DEPOSITS = "все отобранные вклады заполнены до максимальной суммы размещения"
REASON_MAX_BANKS = "использовано предельное число банков"
REASON_BANK_LIMIT = "во всех привлечённых банках достигнут предельный размер размещения средств"
REASON_MIN_SUM = ("остаток меньше минимальной суммы размещения по вкладам, которые ещё могли бы "
                  "его принять с учётом уже применённых к ним ограничений")


def _prepare(deposits, coefs, params, insurance_limit, K):
    """Подбор шага: возвращает (grid, bank_functions, step_increased)."""
    h = base_step(deposits)
    h0 = h
    for _ in range(40):
        grid = build_grid(deposits, coefs, params.amount, h, params.insurance_mode,
                          insurance_limit, params.personal_limit)
        pre_cost = BANK_FUNCTION_WEIGHT * exact_dp.bank_function_cost(grid)
        # предварительная оценка до построения функций банков: в среднем
        # RUNS_PER_DEPOSIT участков линейности на вклад
        n_items = sum(len(b.items) for b in grid.banks)
        rows = K + 2 if K < len(grid.banks) else 1
        pre_total = pre_cost + RUNS_PER_DEPOSIT * n_items * rows * (grid.cells + 1)
        if pre_total <= WORK_BUDGET:
            bfs = [exact_dp.build_bank_function(b, grid.cells, h) for b in grid.banks]
            bfs = [bf for bf in bfs if len(bf.runs) > 0]
            k = min(K, len(bfs))
            work, mem = exact_dp.volume(bfs, grid.cells, k)
            if k < len(bfs):                          # возможен повторный расчёт с K+1
                w2, m2 = exact_dp.volume(bfs, grid.cells, k + 1)
                work, mem = work + w2, max(mem, m2)
            total = work + pre_cost
            if total <= WORK_BUDGET and mem <= MEMORY_BUDGET:
                return grid, bfs, h != h0
            factor = max(math.sqrt(total / WORK_BUDGET), math.sqrt(mem / MEMORY_BUDGET))
        else:
            factor = math.sqrt(pre_total / WORK_BUDGET)
        h = max(h + 1000, int(math.ceil(h * factor * 1.05 / 1000.0)) * 1000)
    raise RuntimeError("Не удалось подобрать шаг дискретизации")


def _cells_to_rub(allocation, h):
    return [(item.deposit, cells * h) for item, cells in allocation]


def _method_result(allocation, h, coefs, start, banks_used) -> MethodResult:
    positions = calc.build_positions(_cells_to_rub(allocation, h), coefs, start)
    income = sum((p.income for p in positions), Decimal("0"))
    placed = sum(p.amount for p in positions)
    return MethodResult(positions, income, placed, banks_used)


def _single_best(deposits, coefs, params, insurance_limit):
    """Доход при размещении всей суммы в один вклад с наибольшим доходом (п. 4.1.1.4).

    Если вся сумма в вклад не помещается (п. 4.1.2.13), берётся наибольшая сумма, которую
    можно в него разместить, и запоминается ограничение, которое не дало разместить больше:
    «max_sum», «insurance» или «personal» (для пояснения пользователю).
    """
    best = (None, 0, Decimal("0"), None)
    for d in deposits:
        amount, limit = params.amount, None
        if d.max_sum is not None and d.max_sum < amount:
            amount, limit = d.max_sum, "max_sum"
        cap = bank_principal_cap_rub(params.insurance_mode, insurance_limit, params.personal_limit)
        if cap is not None and cap < amount:
            amount, limit = cap, ("insurance" if params.personal_limit is None else "personal")
        if params.personal_limit is None and params.insurance_mode == InsuranceMode.WITH_INTEREST:
            with_interest_cap = int(insurance_limit / (1.0 + coefs[d.deposit_id]))
            if with_interest_cap < amount:
                amount, limit = with_interest_cap, "insurance"
        if amount < d.min_sum:
            continue
        income = calc.position_income(amount, coefs[d.deposit_id])
        if income > best[2]:
            best = (d, amount, income, limit)
    return best


def _remainder_reasons(grid: Grid, allocation, banks_used, K, remainder) -> List[str]:
    """Все действующие причины нераспределённого остатка, от старшей к младшей."""
    if remainder <= 0:
        return []
    h = grid.step
    cells = {id(it): c for it, c in allocation}
    items = [it for b in grid.banks for it in b.items]
    used_banks = [b for b in grid.banks if any(id(it) in cells for it in b.items)]
    reasons = []

    def room_in_bank(b):
        """Можно ли добавить в банк хотя бы одну ячейку с учётом его ограничений."""
        used = sum(cells.get(id(it), 0) for it in b.items)
        if b.cap_cells - used < 1:
            return False
        if b.bound_rub is None:
            return True
        with_interest = sum(cells.get(id(it), 0) * h * (1 + it.coef) for it in b.items)
        return any(with_interest + h * (1 + it.coef) <= b.bound_rub for it in b.items)

    # 1. остаток не превышает шага дискретизации — погрешность дискретизации
    if remainder <= h:
        reasons.append(REASON_STEP)
    # 2. все отобранные вклады заполнены до своего максимума
    if items and all(it.deposit.max_sum is not None and
                     cells.get(id(it), 0) >= it.deposit.max_sum // h for it in items):
        reasons.append(REASON_MAX_DEPOSITS)
    # 3. использовано предельное число банков, а свободные банки ещё есть
    if banks_used >= K and K < len(grid.banks):
        reasons.append(REASON_MAX_BANKS)
    # 4. во всех привлечённых банках достигнут предельный размер
    if used_banks and not any(room_in_bank(b) for b in used_banks):
        reasons.append(REASON_BANK_LIMIT)
    # 5. остаток меньше минимальных сумм вкладов, которые ещё могли бы его принять
    open_new_bank = banks_used < K
    candidates = []
    for b in grid.banks:
        is_used = b in used_banks
        if not is_used and not open_new_bank:
            continue
        if is_used and not room_in_bank(b):
            continue
        for it in b.items:
            if id(it) not in cells:
                candidates.append(it.deposit.min_sum)
    if candidates and remainder < min(candidates):
        reasons.append(REASON_MIN_SUM)
    return reasons or [REASON_MIN_SUM]


def optimize(deposits: List[Deposit], params: CalcParams, cbr: CbrInfo,
             insurance_limit: int) -> CalcResult:
    """Полный расчёт распределения. deposits — непустой список отобранных вкладов."""
    notices = []
    start = params.calc_date

    # 1. коэффициенты доходности — один раз, до оптимизации
    coefs: Dict[int, float] = calc.coefficients(deposits, start)

    # 2–3. шаг и точный метод
    grid, bfs, increased = _prepare(deposits, coefs, params, insurance_limit, params.max_banks)
    h = grid.step
    K = min(params.max_banks, len(bfs))
    exact = exact_dp.solve(bfs, grid.cells, K)
    approx = greedy.solve(grid, K)

    if increased:
        # п. 4.1.2.11: суммы округлены с точностью до применённого шага дискретизации
        step = f"{h:,}".replace(",", " ")
        notices.append(f"Суммы в распределении округлены до {step} р., чтобы расчёт выполнился "
                       f"быстро. Это не ошибка и ничего делать не нужно: сумма в каждом вкладе "
                       f"может отличаться от наилучшей не больше чем на {step} р.")
    # 4. оценка распределений подсистемой расчёта
    exact_res = _method_result(exact.allocation, h, coefs, start, exact.banks_used)
    approx_res = _method_result(approx.allocation, h, coefs, start, approx.banks_used)
    # контроль: результат точного метода не должен уступать приближённому (п. 4.1.1.4);
    # уступить он может только в режиме «по сумме с процентами» у банка с большим числом
    # вкладов (п. 4.1.2.14) — тогда принимается распределение приближённого метода
    if approx_res.income > exact_res.income:
        exact, exact_res = approx, approx_res
    tax_years, tax_total = calc.compute_tax(exact_res.positions, params.other_income, start,
                                            cbr.key_rate_current, cbr.key_rate_year_max)
    insured, uninsured = calc.insured_split(exact_res.positions, insurance_limit)

    # 5. дополнительные показатели
    best_dep, best_amount, best_income, best_limit = _single_best(deposits, coefs, params,
                                                                  insurance_limit)

    # прирост при K+1 — если использованы все разрешённые банки и K меньше числа
    # неисключённых банков (п. 4.1.1.4, 4.1.3.3)
    k_gain = None
    not_excluded = params.available_banks or len(bfs)
    if exact.banks_used >= K and params.max_banks < not_excluded:
        more = exact_dp.solve(bfs, grid.cells, K + 1)
        more_res = _method_result(more.allocation, h, coefs, start, more.banks_used)
        k_gain = more_res.income - exact_res.income

    remainder = params.amount - exact_res.placed
    # позиции распределения выводятся по убыванию дохода
    exact_res.positions.sort(key=lambda p: p.income, reverse=True)
    reasons = _remainder_reasons(grid, exact.allocation, exact.banks_used, K, remainder)

    return CalcResult(
        exact=exact_res, approx=approx_res, tax_years=tax_years, tax_total=tax_total,
        income_after_tax=exact_res.income - tax_total, insured=insured, uninsured=uninsured,
        remainder=remainder, remainder_reasons=reasons,
        single_best_deposit=best_dep, single_best_amount=best_amount,
        single_best_income=best_income, single_best_diff=exact_res.income - best_income,
        max_banks=params.max_banks, available_banks=len(bfs),
        k_plus_one_gain=k_gain,
        step=h, step_increased=increased, effective_amount=grid.effective_amount,
        notices=notices, single_best_limit=best_limit)
