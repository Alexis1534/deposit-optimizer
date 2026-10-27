"""Подсистема оптимизации распределения — точка входа (п. 4.1.1.4 ТЗ).

Порядок расчёта:
  1. коэффициенты доходности всех отобранных вкладов — вызов подсистемы расчёта (один раз);
  2. шаг дискретизации и оценка объёма; при превышении — укрупнение шага (п. 4.1.2.11);
  3. точный метод (ДП) и контроль его результата по приближённому (жадному) методу;
  4. выбранное распределение → подсистема расчёта: доходы позиций, НДФЛ, застрахованная часть;
  5. доход при размещении всей суммы в один вклад, прирост при K+1, причина остатка.
Все данные приходят параметрами; модуль не хранит состояния.
"""
import math
from decimal import Decimal
from typing import Dict, List

from app.models import (CalcParams, CalcResult, CbrInfo, Deposit, InsuranceMode,
                        MethodResult)
from app.optimization import exact_dp, greedy
from app.optimization.grid import Grid, base_step, build_grid
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
REASON_BANK_LIMIT = "во всех использованных из справочника банках достигнут предельный размер размещения средств"
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


def _single_best(deposits, coefs, params, start, insurance_limit):
    """Вся сумма — в один вклад с наибольшим доходом (п. 4.1.1.4, 4.1.2.13).

    Учитываются срок, исключённые банки и пределы суммы самого вклада. Страховой лимит
    и предельная сумма в один банк не применяются: вместо этого выводится, какая часть
    средств с процентами застрахована. Возвращает (вклад, доход, застраховано,
    не застраховано, вклад с наибольшей максимальной суммой — если ни один не подходит).
    """
    amount = params.amount
    fits = [d for d in deposits
            if d.min_sum <= amount and (d.max_sum is None or d.max_sum >= amount)]
    if not fits:
        widest = max(deposits, key=lambda d: d.max_sum or 0)
        return None, Decimal("0"), Decimal("0"), Decimal("0"), widest
    best = max(fits, key=lambda d: (calc.position_income(amount, coefs[d.deposit_id]),
                                    -d.deposit_id))
    positions = calc.build_positions([(best, amount)], coefs, start)
    insured, uninsured = calc.insured_split(positions, insurance_limit)
    return best, positions[0].income, insured, uninsured, None


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
    # 4. во всех использованных из справочника банках достигнут предельный размер
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


def _rub(value) -> str:
    """Сумма с разделением разрядов: 1 400 000 или 12 345,67."""
    if isinstance(value, int):
        return f"{value:,}".replace(",", " ")
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def _remainder_tips(reasons: List[str], params: CalcParams, not_excluded: int, k_gain,
                    insurance_limit: int) -> List[str]:
    """Что пользователь может сделать, чтобы разместить остаток. Для остатка в пределах
    погрешности дискретизации советов нет: его не разместить никакими изменениями."""
    if not reasons or reasons[0] == REASON_STEP:
        return []
    tips = []
    if params.max_banks < not_excluded:
        gain = (f": с {params.max_banks + 1} банками доход вырастет на {_rub(k_gain)} р."
                if k_gain is not None and k_gain > 0 else ".")
        tips.append("Увеличьте предельное число банков" + gain)
    if params.excluded_bank_ids:
        tips.append(f"Верните в расчёт исключённые банки ({len(params.excluded_bank_ids)}).")
    if REASON_BANK_LIMIT in reasons:
        if params.personal_limit is not None and params.personal_limit < insurance_limit:
            tips.append(f"Увеличьте предельную сумму в один банк: до страхового лимита "
                        f"({_rub(insurance_limit)} р.) средства в каждом банке застрахованы.")
        elif params.personal_limit is not None:
            tips.append(f"Увеличьте предельную сумму в один банк. Средства сверх страхового лимита "
                        f"({_rub(insurance_limit)} р.) в одном банке не застрахованы.")
        elif params.insurance_mode != InsuranceMode.NONE:
            tips.append(f"Если вы готовы держать в одном банке больше страхового лимита "
                        f"({_rub(insurance_limit)} р.), выберите режим «Без учёта лимита» или задайте "
                        f"свою предельную сумму в один банк. Средства сверх лимита в каждом банке "
                        f"не будут застрахованы.")
    tips.append("Оставшуюся сумму можно разместить самостоятельно: во вкладе или на накопительном "
                "счёте банка, которого нет в справочнике (страховое возмещение действует в каждом "
                "банке отдельно), во вкладах для премиального (VIP) обслуживания или в других "
                "инструментах, например в облигациях федерального займа.")
    return tips


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
    best_dep, best_income, best_insured, best_uninsured, widest = _single_best(
        deposits, coefs, params, start, insurance_limit)
    # нарушает ли один вклад ограничения, выбранные для распределения
    best_breaks_limits = best_dep is not None and (
        (params.personal_limit is not None and params.amount > params.personal_limit) or
        (params.personal_limit is None and params.insurance_mode == InsuranceMode.PRINCIPAL
         and params.amount > insurance_limit) or
        (params.personal_limit is None and params.insurance_mode == InsuranceMode.WITH_INTEREST
         and best_uninsured > 0))

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
    tips = _remainder_tips(reasons, params, not_excluded, k_gain, insurance_limit)

    return CalcResult(
        exact=exact_res, approx=approx_res, tax_years=tax_years, tax_total=tax_total,
        income_after_tax=exact_res.income - tax_total, insured=insured, uninsured=uninsured,
        remainder=remainder, remainder_reasons=reasons,
        single_best_deposit=best_dep, single_best_income=best_income,
        single_best_diff=exact_res.income - best_income,
        single_best_insured=best_insured, single_best_uninsured=best_uninsured,
        single_best_breaks_limits=best_breaks_limits, single_widest_deposit=widest,
        max_banks=params.max_banks, available_banks=len(bfs),
        k_plus_one_gain=k_gain,
        step=h, step_increased=increased, effective_amount=grid.effective_amount,
        notices=notices, remainder_tips=tips)
