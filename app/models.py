"""Структуры данных, которыми обмениваются подсистемы (связи по данным, п. 5.4.6 ТЗ).

«Банк» и «Вклад» — отображение записей таблиц БД.
«Параметры расчёта», «Результат расчёта», «Позиция распределения» — структуры данных
сеанса в оперативной памяти (п. 4.1.2.12), в БД не сохраняются.
Модуль не содержит логики и не хранит состояния.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import IntEnum
from typing import Dict, List, Optional


class InsuranceMode(IntEnum):
    """Режим учёта предельного размера страхового возмещения (п. 4.1.2.7)."""
    NONE = 1            # не учитывается
    PRINCIPAL = 2       # вложенная сумма в одном банке не превышает лимита (по умолчанию)
    WITH_INTEREST = 3   # вложенная сумма вместе с процентами не превышает лимита


@dataclass(frozen=True)
class Deposit:
    """Банковский вклад вместе со сведениями о банке."""
    deposit_id: int
    bank_id: int
    bank_name: str
    bank_license: str              # справочник содержит только банки — участники ССВ (п. 4.1.2.1)
    name: str
    rate: float                    # % годовых
    term_months: int
    min_sum: int
    max_sum: Optional[int]         # None — без верхнего ограничения
    capitalization: bool
    actual_date: date


@dataclass(frozen=True)
class IntegrityIssue:
    """Нарушение целостности справочника (п. 4.2.3)."""
    record: str
    problem: str


@dataclass
class CbrInfo:
    """Сведения Банка России (п. 4.1.1.5)."""
    key_rate_current: float
    key_rate_as_of: str
    key_rate_year_max: Dict[int, float]
    avg_max_rate: Optional[float]       # None — значение не получено, проверка правдоподобия не выполняется
    avg_max_as_of: str
    from_cache: bool
    message: str = ""


@dataclass
class CalcParams:
    """Параметры расчёта (п. 4.1.2.3)."""
    amount: int
    term_from: int
    term_to: int
    excluded_bank_ids: frozenset
    max_banks: int                          # K, уже ограниченное числом неисключённых банков
    insurance_mode: InsuranceMode
    personal_limit: Optional[int]           # None — признак учёта не задан
    other_income: Decimal                   # р., с копейками
    calc_date: date
    available_banks: int = 0                # число неисключённых банков (0 — не задано)


@dataclass
class Position:
    """Позиция распределения (п. 4.1.3.1)."""
    deposit: Deposit
    amount: int
    income: Decimal                         # доход по позиции, округлён до копеек
    end_date: date
    income_by_year: Dict[int, Decimal] = field(default_factory=dict)


@dataclass
class MethodResult:
    """Результат одного метода решения (точного или приближённого)."""
    positions: List[Position]
    income: Decimal
    placed: int
    banks_used: int


@dataclass
class TaxYear:
    year: int
    deposit_income: Decimal
    other_income: Decimal
    non_taxable: Decimal
    base: Decimal
    tax: Decimal
    high_part: Decimal = Decimal("0")     # часть базы сверх порога 2,4 млн р. (ставка 15 %)
    scale: str = "13 %"
    tax_full: Decimal = Decimal("0")      # налог за год с учётом дохода по вкладам
    tax_without: Decimal = Decimal("0")   # налог за год без дохода по вкладам
    carried: Decimal = Decimal("0")       # перенос необлагаемой суммы с предшествующего года
    # поле tax — НДФЛ, приходящийся на доход по вкладам (п. 4.1.2.6)


@dataclass
class CalcResult:
    """Результат расчёта (п. 4.1.3.2–4.1.3.4)."""
    exact: MethodResult
    approx: MethodResult
    tax_years: List[TaxYear]
    tax_total: Decimal
    income_after_tax: Decimal
    insured: Decimal                        # вклады с процентами в пределах лимита по банкам
    uninsured: Decimal
    remainder: int
    remainder_reasons: List[str]            # от старшей причины к младшей; пусто — остатка нет
    single_best_deposit: Optional[Deposit]  # вклад, принимающий всю сумму; None — такого нет
    single_best_income: Decimal
    single_best_diff: Decimal               # доход распределения − доход в один вклад
    single_best_insured: Decimal            # вклад с процентами в пределах страхового лимита
    single_best_uninsured: Decimal
    single_best_breaks_limits: bool         # превышает лимит или предельную сумму в один банк
    single_widest_deposit: Optional[Deposit]  # наибольшая максимальная сумма, если вклада нет
    max_banks: int
    available_banks: int
    k_plus_one_gain: Optional[Decimal]      # только при использовании всех K банков
    step: int
    step_increased: bool
    effective_amount: int
    notices: List[str] = field(default_factory=list)
    # что пользователь может изменить, чтобы уменьшить нераспределённый остаток
    remainder_tips: List[str] = field(default_factory=list)
