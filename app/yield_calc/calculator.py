"""Расчёт доходности: проценты, НДФЛ, застрахованная часть. Состояния не хранит."""
import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Tuple

from app.models import Deposit, Position, TaxYear

KOPECK = Decimal("0.01")
NON_TAXABLE_BASE = Decimal("1000000")
# Шкала НДФЛ для процентов по вкладам: 13 % до порога и 15 % с суммы сверх него
# (пятиступенчатая шкала относится к основной базе и сюда не применяется).
NDFL_THRESHOLD = Decimal("2400000")
NDFL_RATE_BASE = Decimal("0.13")
NDFL_RATE_HIGH = Decimal("0.15")


def add_months(d: date, months: int) -> date:
    """Дата через заданное число месяцев; день обрезается до длины месяца (31.01 + 1 мес. = 28.02)."""
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def end_date(start: date, term_months: int) -> date:
    return add_months(start, term_months)


DAY = timedelta(days=1)


def _year_fraction(d1: date, d2: date) -> float:
    """Сумма дней / длина года по каждому календарному году (365 или 366).

    Считаются дни периода (d1, d2]: день открытия не входит, день окончания входит
    (ст. 839 ГК РФ). Период через Новый год делится по годам.
    """
    total = 0.0
    cur, d2 = d1 + DAY, d2 + DAY          # (d1, d2] == [d1 + 1, d2 + 1)
    while cur < d2:
        next_new_year = date(cur.year + 1, 1, 1)
        part_end = min(d2, next_new_year)
        year_len = 366 if calendar.isleap(cur.year) else 365
        total += (part_end - cur).days / year_len
        cur = part_end
    return total


def income_coefficient(deposit: Deposit, start: date) -> float:
    """Доход на 1 рубль вложений за весь срок, без округления.

    Без капитализации: r * сумма(дни / длина года). С капитализацией: проценты
    начисляются каждый месяц, итог - произведение (1 + r * доля года) минус 1.
    """
    r = deposit.rate / 100.0
    finish = end_date(start, deposit.term_months)
    if not deposit.capitalization:
        return r * _year_fraction(start, finish)
    factor = 1.0
    for i in range(deposit.term_months):
        m_start = add_months(start, i)
        m_end = add_months(start, i + 1)
        factor *= 1.0 + r * _year_fraction(m_start, m_end)
    return factor - 1.0


def income_by_year(deposit: Deposit, amount: int, total: Decimal, start: date) -> Dict[int, Decimal]:
    """Доход позиции по календарным годам.

    Без капитализации всё относится к году окончания срока. С капитализацией - к годам,
    в которых проценты начислены; последний год забирает разницу округления.
    """
    finish = end_date(start, deposit.term_months)
    if not deposit.capitalization:
        return {finish.year: total}

    r = deposit.rate / 100.0
    parts, balance = {}, float(amount)
    for i in range(deposit.term_months):
        m_start, m_end = add_months(start, i), add_months(start, i + 1)
        accrued = balance * r * _year_fraction(m_start, m_end)
        balance += accrued
        parts[m_end.year] = parts.get(m_end.year, 0.0) + accrued

    years = sorted(parts)
    result, used = {}, Decimal("0")
    for year in years[:-1]:
        result[year] = round_money(parts[year])
        used += result[year]
    result[years[-1]] = total - used            # чтобы сумма совпала с доходом позиции
    return result


def coefficients(deposits: List[Deposit], start: date) -> Dict[int, float]:
    """Коэффициенты доходности вкладов: {deposit_id: доход на 1 рубль}."""
    return {d.deposit_id: income_coefficient(d, start) for d in deposits}


def round_money(value) -> Decimal:
    """Округление до копеек (половина вверх)."""
    return Decimal(str(value)).quantize(KOPECK, rounding=ROUND_HALF_UP)


def position_income(amount: int, coefficient: float) -> Decimal:
    """Доход по позиции, округлённый до копеек."""
    return round_money(amount * coefficient)


def build_positions(allocation: List[Tuple[Deposit, int]], coefs: Dict[int, float],
                    start: date) -> List[Position]:
    """Позиции распределения с доходом и датой окончания."""
    result = []
    for dep, amount in allocation:
        if amount <= 0:
            continue
        income = position_income(amount, coefs[dep.deposit_id])
        result.append(Position(deposit=dep, amount=amount, income=income,
                               end_date=end_date(start, dep.term_months),
                               income_by_year=income_by_year(dep, amount, income, start)))
    return result


CARRYOVER_MIN_TERM = 15        # перенос только для вкладов дольше 15 месяцев


def carryover_caps(deposit: Deposit, amount: int, start: date) -> Dict[int, Decimal]:
    """Пределы переноса необлагаемой суммы по вкладу: {год-источник: предел}.

    Предел года = сумма * ставка * дни начисления в году / длина года, то есть проценты,
    фактически начисленные за этот год. Пустой словарь, если вклад с капитализацией или
    срок не больше 15 месяцев.
    """
    if deposit.capitalization or deposit.term_months <= CARRYOVER_MIN_TERM:
        return {}
    finish = end_date(start, deposit.term_months)
    r = deposit.rate / 100.0
    first = start + DAY                       # первый день начисления
    caps = {}
    for year in range(first.year, finish.year):
        # дни начисления в году: (max(start, 31.12 пред. года), min(finish, 31.12 года)]
        year_from = max(start, date(year, 1, 1) - DAY)
        year_to = min(finish, date(year, 12, 31))
        if year_to > year_from:
            caps[year] = round_money(amount * r * _year_fraction(year_from, year_to))
    return caps


def _tax_on(base: Decimal) -> Tuple[Decimal, Decimal]:
    """(налог по шкале, часть базы сверх порога) для положительной части базы."""
    positive = max(base, Decimal("0"))
    low = min(positive, NDFL_THRESHOLD)
    high = positive - low
    return round_money(low * NDFL_RATE_BASE + high * NDFL_RATE_HIGH), high


def compute_tax(positions: List[Position], other_income, calc_date: date,
                key_rate_current: float, key_rate_year_max: Dict[int, float]):
    """Оценка НДФЛ по годам. Возвращает (список TaxYear, НДФЛ на распределение).

    Необлагаемая сумма года - 1 000 000 * наибольшая ключевая ставка на 1-е число месяцев
    года. К базе текущего года добавляется other_income. Неиспользованные необлагаемые
    суммы лет, прошедших до окончания длинного вклада без капитализации, переносятся
    в год окончания (в пределах carryover_caps). Выводится не весь налог, а разность
    налога с вкладами и без них.
    """
    by_year = defaultdict(lambda: Decimal("0"))
    for p in positions:
        for year, part in p.income_by_year.items():
            by_year[year] += part
    current_year = calc_date.year
    by_year.setdefault(current_year, Decimal("0"))   # текущий год показываем всегда

    # пределы переноса: {год окончания: {год-источник: предел}}
    caps = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    for p in positions:
        for source, cap in carryover_caps(p.deposit, p.amount, calc_date).items():
            caps[p.end_date.year][source] += cap
            by_year.setdefault(source, Decimal("0"))

    def non_taxable_for(year):
        # для месяцев, которые ещё не наступили, берётся текущая ставка
        if year == current_year:
            rate = max(key_rate_year_max.get(year, key_rate_current), key_rate_current)
        else:
            rate = key_rate_current
        return round_money(NON_TAXABLE_BASE * Decimal(str(rate)) / 100)

    years, total = [], Decimal("0")
    pool = {}                       # {год: неиспользованный остаток необлагаемой суммы}
    for year in sorted(by_year):
        non_taxable = non_taxable_for(year)
        other = Decimal(str(other_income)) if year == current_year else Decimal("0")
        income_of_year = by_year[year] + other

        # остатки прошлых лет тратим от ранних к поздним
        carried = Decimal("0")
        for source in sorted(pool):
            if source >= year:
                break
            take = min(pool[source], caps[year][source])
            if take > 0:
                pool[source] -= take
                caps[year][source] -= take
                carried += take
        pool[year] = max(non_taxable - income_of_year, Decimal("0"))
        base = income_of_year - non_taxable - carried
        tax_with, high = _tax_on(base)
        tax_without, _ = _tax_on(other - non_taxable)     # налог без вкладов
        increment = tax_with - tax_without
        years.append(TaxYear(year, by_year[year], other, non_taxable, base, increment,
                             high_part=high, scale="13 %" if high == 0 else "13 % / 15 %",
                             tax_full=tax_with, tax_without=tax_without, carried=carried))
        total += increment
    return years, total


def insured_split(positions: List[Position], insurance_limit: int) -> Tuple[Decimal, Decimal]:
    """Застрахованная и незастрахованная суммы на конец срока (вклад с процентами).

    По каждому банку застраховано min(вложено + проценты, лимит).
    """
    per_bank = defaultdict(Decimal)
    for p in positions:
        per_bank[p.deposit.bank_id] += Decimal(p.amount) + p.income
    total = sum(per_bank.values(), Decimal("0"))
    insured = sum((min(s, Decimal(insurance_limit)) for s in per_bank.values()), Decimal("0"))
    return insured, total - insured
