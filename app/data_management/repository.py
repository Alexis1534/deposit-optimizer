"""Подсистема управления данными (п. 4.1.1.2 ТЗ).

Чтение справочника банков и вкладов, контроль его целостности (п. 4.2.3), отбор вкладов
по параметрам, наиболее ранняя дата актуальности. Вызывается только подсистемой
интерфейса; к подсистемам расчёта и внешних источников не обращается (карта Константайна).
"""
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Tuple

from app.models import Deposit, IntegrityIssue

TERM_MIN, TERM_MAX = 1, 18
SUSPICIOUS_MARGIN_PP = 3.0     # запас над средней максимальной ставкой топ-10 банков, п.п.


@dataclass
class Catalog:
    """Справочник после контроля целостности."""
    deposits: List[Deposit]            # только записи, прошедшие проверку
    banks: List[Tuple[int, str]]       # (bank_id, наименование) банков корректных вкладов
    issues: List[IntegrityIssue]
    insurance_limit: int
    earliest_actual_date: Optional[date]


def is_suspicious(rate: float, avg_max_rate: Optional[float]) -> bool:
    """Проверка правдоподобия процентной ставки (п. 4.2.4).

    Ставка считается подозрительной, если превышает среднюю максимальную ставку
    десяти банков с наибольшим объёмом вкладов более чем на SUSPICIOUS_MARGIN_PP пункта.
    Такие записи не отвергаются, а помечаются: сведения могли устареть или быть внесены неверно.
    Если средняя максимальная ставка не получена, проверка не выполняется (п. 4.1.1.2).
    """
    if avg_max_rate is None:
        return False
    return rate > avg_max_rate + SUSPICIOUS_MARGIN_PP


def _connect(db_path: str) -> sqlite3.Connection:
    # только чтение: целостность файла БД не может быть нарушена Системой (п. 4.2.6)
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def load_catalog(db_path: str) -> Catalog:
    """Чтение справочника с проверкой каждой записи; некорректные записи в расчёт не идут."""
    con = _connect(db_path)
    try:
        banks = {row[0]: row for row in con.execute(
            "SELECT bank_id, name, license_no FROM bank")}
        rows = con.execute(
            "SELECT deposit_id, bank_id, name, rate, term_months, min_sum, max_sum, "
            "capitalization, actual_date FROM deposit ORDER BY deposit_id").fetchall()
        limit_row = con.execute(
            "SELECT value FROM setting WHERE key = 'insurance_limit'").fetchone()
    finally:
        con.close()

    issues: List[IntegrityIssue] = []
    # контроль записей о банках: наименование, заполненность и уникальность номера лицензии
    licenses = {}
    for bank_id, name, lic in banks.values():
        key = str(lic).strip() if lic is not None else ""
        if key:
            licenses.setdefault(key, []).append(bank_id)
    bad_banks = {}
    for bank_id, name, lic in banks.values():
        problems = []
        if not (name or "").strip():
            problems.append("не заполнено наименование банка")
        key = str(lic).strip() if lic is not None else ""
        if not key:
            problems.append("не заполнен номер лицензии ЦБ РФ")
        elif len(licenses[key]) > 1:
            others = ", ".join(f"№ {b}" for b in licenses[key] if b != bank_id)
            problems.append(f"номер лицензии {key} совпадает с номером банка {others}")
        if problems:
            bad_banks[bank_id] = problems
            label = f"банк № {bank_id}" + (f" «{name.strip()}»" if (name or "").strip() else "")
            issues.extend(IntegrityIssue(label, p) for p in problems)

    deposits: List[Deposit] = []
    for dep_id, bank_id, name, rate, term, min_sum, max_sum, cap, actual in rows:
        label = f"вклад № {dep_id}" + (f" «{name}»" if name else "")
        problems = []
        bank = banks.get(bank_id)
        if bank is None:
            problems.append(f"нет банка, на который ссылается запись (код банка {bank_id})")
        elif bank_id in bad_banks:
            problems.append(f"запись о банке № {bank_id} не прошла проверку")
        if not (name or "").strip():
            problems.append("не заполнено наименование вклада")
        if rate is None or not isinstance(rate, (int, float)) or rate <= 0:
            problems.append(f"процентная ставка должна быть положительной (указано: {rate})")
        if not isinstance(term, int) or not TERM_MIN <= term <= TERM_MAX:
            problems.append(f"срок размещения вне диапазона {TERM_MIN}–{TERM_MAX} мес. (указано: {term})")
        if not isinstance(min_sum, int) or min_sum < 0:
            problems.append(f"некорректная минимальная сумма (указано: {min_sum})")
        elif max_sum is not None and (not isinstance(max_sum, int) or min_sum > max_sum):
            problems.append(f"минимальная сумма {min_sum} превышает максимальную {max_sum}")
        actual_date = None
        if not (actual or "").strip():
            problems.append("не заполнена дата актуальности сведений")
        else:
            try:
                actual_date = date.fromisoformat(actual.strip())
            except ValueError:
                problems.append(f"дата актуальности не в формате ГГГГ-ММ-ДД (указано: {actual})")
        if problems:
            issues.extend(IntegrityIssue(label, p) for p in problems)
            continue
        deposits.append(Deposit(
            deposit_id=dep_id, bank_id=bank_id, bank_name=bank[1].strip(),
            bank_license=str(bank[2]).strip(),
            name=name.strip(), rate=float(rate), term_months=term, min_sum=min_sum,
            max_sum=max_sum, capitalization=bool(cap), actual_date=actual_date))

    bank_list = sorted({(d.bank_id, d.bank_name) for d in deposits}, key=lambda b: b[1])
    earliest = min((d.actual_date for d in deposits), default=None)
    insurance_limit = int(limit_row[0]) if limit_row else 1_400_000
    return Catalog(deposits, bank_list, issues, insurance_limit, earliest)


def select_deposits(deposits: List[Deposit], amount: int, term_from: int, term_to: int,
                    excluded_bank_ids: frozenset) -> List[Deposit]:
    """Отбор вкладов по сумме (минимальная сумма не больше суммы размещения),
    диапазону срока и перечню неисключённых банков."""
    return [d for d in deposits
            if d.bank_id not in excluded_bank_ids
            and term_from <= d.term_months <= term_to
            and d.min_sum <= amount]
