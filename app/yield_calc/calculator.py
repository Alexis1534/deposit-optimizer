"""Подсистема расчёта доходности (п. 4.1.1.3 ТЗ).

Содержит все правила начисления процентов, расчёта НДФЛ и определения застрахованной
части. Подсистема оптимизации этих правил не содержит и обращается сюда:
  * до поиска — за коэффициентом доходности каждого вклада;
  * после поиска — с выбранным распределением за налогом и итогами.
Модуль не хранит состояния: все данные приходят параметрами.
"""
import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Tuple

from app.models import Deposit, Position, TaxYear

KOPECK = Decimal("0.01")
NON_TAXABLE_BASE = Decimal("1000000")
# Шкала НДФЛ для отдельной налоговой базы по процентам (с 2025 г.):
# 13 % в пределах порога и 15 % с суммы превышения. Пятиступенчатая шкала
# (13/15/18/20/22 %) к процентам по вкладам не применяется — она для основной базы.
NDFL_THRESHOLD = Decimal("2400000")
NDFL_RATE_BASE = Decimal("0.13")
NDFL_RATE_HIGH = Decimal("0.15")


# --------------------------------------------------------------------------- даты
def add_months(d: date, months: int) -> date:
    """Дата через заданное число месяцев; день ограничивается длиной месяца (31.01 + 1 = 28/29.02)."""
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def end_date(start: date, term_months: int) -> date:
    return add_months(start, term_months)


DAY = timedelta(days=1)


def _year_fraction(d1: date, d2: date) -> float:
    """Σ дней / длина года по каждому календарному году отдельно (365 или 366, п. 4.1.2.5).

    Проценты начисляются со дня, следующего за днём поступления вклада, по день возврата
    включительно (п. 1 ст. 839 ГК РФ), то есть за дни периода (d1, d2]. Период, пересекающий
    Новый год, делится по годам: 31 декабря относится к уходящему году, 1 января — к новому.
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


# ------------------------------------------------------------ доход по вкладу
def income_coefficient(deposit: Deposit, start: date) -> float:
    """Доход на 1 рубль вложений за весь срок (без округления).

    Доход линейно пропорционален сумме, поэтому коэффициент считается один раз до
    оптимизации.
      * без капитализации — простые проценты: r · Σ(дни_года / длина_года);
      * с капитализацией — ежемесячное начисление с фактическим числом дней каждого
        месяца: ∏(1 + r · доля_года_месяца) − 1.
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
    """Распределение дохода позиции по календарным годам (п. 4.1.2.6).

    Без капитализации доход относится к году окончания срока: проценты выплачиваются
    в конце. С капитализацией проценты относятся к годам, в которых они фактически
    капитализированы: сумма процентов, капитализированных в течение года, — доход этого года.
    Сумма по годам совпадает с доходом позиции: последний год принимает разницу округления.
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
    result[years[-1]] = total - used            # остаток, чтобы сумма совпала с доходом позиции
    return result


def coefficients(deposits: List[Deposit], start: date) -> Dict[int, float]:
    """Коэффициенты доходности для списка вкладов: {deposit_id: доход на 1 рубль}."""
    return {d.deposit_id: income_coefficient(d, start) for d in deposits}


def round_money(value) -> Decimal:
    """Округление до копеек по правилам математического округления."""
    return Decimal(str(value)).quantize(KOPECK, rounding=ROUND_HALF_UP)


def position_income(amount: int, coefficient: float) -> Decimal:
    """Доход по позиции распределения, округлённый до копеек."""
    return round_money(amount * coefficient)


def build_positions(allocation: List[Tuple[Deposit, int]], coefs: Dict[int, float],
                    start: date) -> List[Position]:
    """Оценка выбранного распределения: доход и дата окончания по каждой позиции."""
    result = []
    for dep, amount in allocation:
        if amount <= 0:
            continue
        income = position_income(amount, coefs[dep.deposit_id])
        result.append(Position(deposit=dep, amount=amount, income=income,
                               end_date=end_date(start, dep.term_months),
                               income_by_year=income_by_year(dep, amount, income, start)))
    return result


# --------------------------------------------------------------------- НДФЛ
CARRYOVER_MIN_TERM = 15        # перенос действует для вкладов дольше 15 месяцев


def carryover_caps(deposit: Deposit, income: Decimal, start: date) -> Dict[int, Decimal]:
    """Пределы переноса по одному вкладу: доход, приходящийся по дням размещения на каждый
    календарный год срока, предшествующий году завершения (п. 4.1.2.6 ТЗ, п. 1 ст. 214.2 НК).

    Неиспользованная необлагаемая сумма такого года может быть перенесена в год завершения
    срока, но не более этой величины. Пустой словарь — вклад под правило не подпадает
    (капитализация процентов или срок не больше 15 месяцев).
    """
    if deposit.capitalization or deposit.term_months <= CARRYOVER_MIN_TERM:
        return {}
    finish = end_date(start, deposit.term_months)
    total_days = Decimal((finish - start).days)
    first, stop = start + DAY, finish + DAY     # дни начисления (start, finish], ст. 839 ГК РФ
    caps = {}
    for year in range(first.year, finish.year):
        year_start = max(first, date(year, 1, 1))
        year_end = min(stop, date(year + 1, 1, 1))
        days = (year_end - year_start).days
        if days > 0:
            caps[year] = round_money(income * Decimal(days) / total_days)
    return caps


def _tax_on(base: Decimal) -> Tuple[Decimal, Decimal]:
    """(налог по шкале, часть базы сверх порога) для положительной части базы."""
    positive = max(base, Decimal("0"))
    low = min(positive, NDFL_THRESHOLD)
    high = positive - low
    return round_money(low * NDFL_RATE_BASE + high * NDFL_RATE_HIGH), high


def compute_tax(positions: List[Position], other_income, calc_date: date,
                key_rate_current: float, key_rate_year_max: Dict[int, float]):
    """Оценочная величина НДФЛ (п. 4.1.2.6 ТЗ).

    * доход позиции относится к годам по правилам income_by_year;
    * необлагаемая сумма = 1 000 000 × наибольшая ключевая ставка на 1-е число месяцев года
      (п. 1 ст. 214.2 НК РФ); для месяцев, не наступивших на дату расчёта, — последнее
      полученное значение;
    * база текущего года увеличивается на процентный доход по вкладам вне расчёта;
    * для вклада без капитализации со сроком больше 15 месяцев неиспользованные части
      необлагаемых сумм всех лет срока размещения накапливаются и уменьшают базу года
      завершения срока; вклад берёт из каждого такого года не больше дохода, приходящегося
      на этот год по дням размещения; накопленный остаток года, не израсходованный вкладами,
      остаётся доступным для более поздних лет завершения;
    * налог: 13 % с базы в пределах 2 400 000 р. и 15 % с части сверх этого значения;
    * пользователю выводится НДФЛ, приходящийся на доход по вкладам: разность
      между налогом с учётом этого дохода и налогом без него.
    Возвращает (список TaxYear, НДФЛ на распределение в целом).
    """
    by_year = defaultdict(lambda: Decimal("0"))
    for p in positions:
        for year, part in p.income_by_year.items():
            by_year[year] += part
    current_year = calc_date.year
    by_year.setdefault(current_year, Decimal("0"))   # текущий год показывается всегда

    # пределы переноса: {год завершения: {год-источник: предел}} по всем подпадающим вкладам
    caps = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    for p in positions:
        for source, cap in carryover_caps(p.deposit, p.income, calc_date).items():
            caps[p.end_date.year][source] += cap
            by_year.setdefault(source, Decimal("0"))                # год-источник переноса

    def non_taxable_for(year):
        # наибольшая ставка на 1-е число месяцев года; для месяцев, не наступивших на дату
        # расчёта, принимается последнее полученное значение (п. 4.1.2.6), поэтому для
        # текущего года — максимум из уже известных значений и текущей ставки, для будущих — текущая
        if year == current_year:
            rate = max(key_rate_year_max.get(year, key_rate_current), key_rate_current)
        else:
            rate = key_rate_current
        return round_money(NON_TAXABLE_BASE * Decimal(str(rate)) / 100)

    years, total = [], Decimal("0")
    pool = {}                       # {год-источник: неизрасходованный остаток необлагаемой суммы}
    for year in sorted(by_year):
        non_taxable = non_taxable_for(year)
        other = Decimal(str(other_income)) if year == current_year else Decimal("0")
        income_of_year = by_year[year] + other

        # накопленные остатки прошлых лет расходуются от ранних к поздним
        carried = Decimal("0")
        for source in sorted(pool):
            if source >= year:
                break
            take = min(pool[source], caps[year][source])
            if take > 0:
                pool[source] -= take
                caps[year][source] -= take
                carried += take
        # остаток необлагаемой суммы этого года пополняет накопление
        pool[year] = max(non_taxable - income_of_year, Decimal("0"))
        base = income_of_year - non_taxable - carried
        tax_with, high = _tax_on(base)
        tax_without, _ = _tax_on(other - non_taxable)     # налог, если бы вкладов не было
        increment = tax_with - tax_without
        years.append(TaxYear(year, by_year[year], other, non_taxable, base, increment,
                             high_part=high, scale="13 %" if high == 0 else "13 % / 15 %",
                             tax_full=tax_with, tax_without=tax_without, carried=carried))
        total += increment
    return years, total


# ---------------------------------------------- застрахованная часть средств
def insured_split(positions: List[Position], insurance_limit: int) -> Tuple[Decimal, Decimal]:
    """Застрахованная и незастрахованная суммы на конец срока — вклад вместе с процентами.

    Страховое возмещение выплачивается по вкладу с начисленными процентами в пределах
    лимита на один банк, поэтому проценты, умещающиеся в лимит, тоже застрахованы.
    Считается по каждому банку: min(вложено + проценты, лимит).
    """
    per_bank = defaultdict(Decimal)
    for p in positions:
        per_bank[p.deposit.bank_id] += Decimal(p.amount) + p.income
    total = sum(per_bank.values(), Decimal("0"))
    insured = sum((min(s, Decimal(insurance_limit)) for s in per_bank.values()), Decimal("0"))
    return insured, total - insured
