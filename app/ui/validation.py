"""Контроль вводимой информации (п. 4.2.1, 4.2.2 ТЗ; таблица 1 тестов).

При ошибке расчёт не выполняется, сообщение указывает поле и характер ошибки,
введённые значения сохраняются (форма перерисовывается с исходными строками).
"""
import re
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import date
from typing import Dict, List, Optional

from app.models import CalcParams, InsuranceMode

AMOUNT_MIN, AMOUNT_MAX = 1_000, 100_000_000
TERM_MIN, TERM_MAX = 1, 18
PERSONAL_MIN = 1_000
OTHER_INCOME_MAX = Decimal("1000000000")   # 1 млрд р.; граница выбрана разработчиком
_MONEY_RE = re.compile(r"^\d+(?:[.,]\d{1,2})?$")

FIELD_NAMES = {
    "amount": "Сумма размещения",
    "term_from": "Срок размещения: от",
    "term_to": "Срок размещения: до",
    "banks": "Перечень банков",
    "max_banks": "Предельное число банков",
    "insurance_mode": "Режим учёта страхового лимита",
    "personal_limit": "Предельная сумма в один банк",
    "other_income": "Проценты, уже полученные в этом году",
}


@dataclass
class FormState:
    """Введённые пользователем значения в исходном виде — для повторного показа формы."""
    amount: str = ""
    term_from: str = "1"
    term_to: str = "18"
    excluded: frozenset = frozenset()
    max_banks: str = ""
    insurance_mode: str = str(int(InsuranceMode.PRINCIPAL))
    personal_enabled: bool = False
    personal_limit: str = ""
    other_income: str = "0"


@dataclass
class ValidationResult:
    params: Optional[CalcParams]
    errors: Dict[str, str] = field(default_factory=dict)
    notices: List[str] = field(default_factory=list)


def form_from_request(form) -> FormState:
    excluded = set()
    for v in form.getlist("exclude"):
        if v.isdigit():
            excluded.add(int(v))
    return FormState(
        amount=form.get("amount", "").strip(),
        term_from=form.get("term_from", "").strip(),
        term_to=form.get("term_to", "").strip(),
        excluded=frozenset(excluded),
        max_banks=form.get("max_banks", "").strip(),
        insurance_mode=form.get("insurance_mode", "").strip(),
        personal_enabled=form.get("personal_enabled") == "on",
        personal_limit=form.get("personal_limit", "").strip(),
        other_income=form.get("other_income", "").strip(),
    )


def _parse_int(raw: str) -> Optional[int]:
    """Целое число; допускаются пробелы между разрядами. None — неверный числовой формат."""
    s = raw.replace(" ", "").replace("\u00a0", "")
    if not s or not s.isdigit():
        return None
    return int(s)


def _parse_money(raw: str) -> Optional[Decimal]:
    """Сумма в рублях с копейками: 1500, 1 500, 1500,5, 1500.50. None — неверный формат."""
    s = raw.replace(" ", "").replace("\u00a0", "")
    if not _MONEY_RE.match(s):
        return None
    return Decimal(s.replace(",", "."))


def validate(state: FormState, all_bank_ids: List[int], today: date) -> ValidationResult:
    errors: Dict[str, str] = {}
    notices: List[str] = []

    def number(key, lo=None, hi=None, allow_empty_default=None):
        raw = getattr(state, key)
        if raw == "" and allow_empty_default is not None:
            return allow_empty_default
        value = _parse_int(raw)
        if value is None:
            errors[key] = "требуется целое неотрицательное число без дробной части" if raw \
                else "поле не заполнено"
            return None
        if lo is not None and value < lo:
            errors[key] = f"значение меньше допустимого ({lo:,})".replace(",", " ")
            return None
        if hi is not None and value > hi:
            errors[key] = f"значение превышает допустимое ({hi:,})".replace(",", " ")
            return None
        return value

    amount = number("amount", AMOUNT_MIN, AMOUNT_MAX)
    t_from = number("term_from", TERM_MIN, TERM_MAX)
    t_to = number("term_to", TERM_MIN, TERM_MAX)
    if t_from is not None and t_to is not None and t_from > t_to:
        errors["term_from"] = "нижняя граница срока больше верхней"

    remaining = [b for b in all_bank_ids if b not in state.excluded]
    if not remaining:
        errors["banks"] = "исключены все банки — не осталось ни одного банка для рассмотрения"

    k = number("max_banks", 1, None, allow_empty_default=len(remaining) or 1)
    if k is not None and remaining and k > len(remaining):
        notices.append(f"Предельное число банков {k} больше числа неисключённых банков; "
                       f"принято {len(remaining)}.")
        k = len(remaining)

    mode = None
    if state.insurance_mode in {str(int(m)) for m in InsuranceMode}:
        mode = InsuranceMode(int(state.insurance_mode))
    else:
        errors["insurance_mode"] = "режим не выбран"

    personal = None
    if state.personal_enabled:
        personal = number("personal_limit", PERSONAL_MIN)

    other = Decimal("0")
    if state.other_income != "":
        other = _parse_money(state.other_income)
        if other is None:
            errors["other_income"] = ("требуется неотрицательная сумма в рублях, копейки — "
                                      "не более двух знаков после запятой (например, 1 500,50)")
        elif other > OTHER_INCOME_MAX:
            errors["other_income"] = "значение превышает допустимое (1 000 000 000)"

    if errors:
        return ValidationResult(None, errors, notices)
    return ValidationResult(CalcParams(
        amount=amount, term_from=t_from, term_to=t_to, excluded_bank_ids=state.excluded,
        max_banks=k, insurance_mode=mode, personal_limit=personal, other_income=other,
        calc_date=today, available_banks=len(remaining)), errors, notices)
