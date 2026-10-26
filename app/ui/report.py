"""Форматирование величин (п. 4.1.3.7) и текстовый отчёт для сохранения (п. 4.5.4)."""
from decimal import Decimal

from app import clock
from app.data_management.repository import is_suspicious
from app.models import CalcParams, CalcResult, InsuranceMode

NBSP = "\u00a0"

MODE_TITLES = {
    InsuranceMode.NONE: "без учёта лимита",
    InsuranceMode.PRINCIPAL: "по вложенной сумме",
    InsuranceMode.WITH_INTEREST: "по сумме с процентами",
}
MODE_HINTS = {
    InsuranceMode.NONE: "страховой лимит не учитывается",
    InsuranceMode.PRINCIPAL: "вложенная сумма в одном банке не больше лимита",
    InsuranceMode.WITH_INTEREST: "вложенная сумма вместе с процентами в одном банке не больше лимита",
}


def money(value) -> str:
    """1 234 567,89 — разделение разрядов и два десятичных знака."""
    q = Decimal(str(value)).quantize(Decimal("0.01"))
    sign = "−" if q < 0 else ""
    whole, frac = f"{abs(q):.2f}".split(".")
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    return f"{sign}{NBSP.join(groups)},{frac}"


def rate(value) -> str:
    return f"{float(value):.2f}".replace(".", ",")


def single_deposit_summary(result: CalcResult, params: CalcParams, insurance_limit: int) -> dict:
    """Показатель «Если положить всё на один вклад» (п. 4.1.2.13) — общий для страницы и файла."""
    d = result.single_best_deposit
    if d is None:
        w = result.single_widest_deposit
        return dict(title="Если положить всё на один вклад", income=None, comparison="", notes=[
            f"Всю сумму в одном вкладе разместить нельзя: ни один вклад не принимает "
            f"{money(params.amount)} р. Наибольшая максимальная сумма — {money(w.max_sum)} р. "
            f"(«{w.name}», {w.bank_name})."])
    notes = []
    if result.single_best_uninsured > 0:
        notes.append(f"Застраховано {money(result.single_best_insured)} р., не застраховано "
                     f"{money(result.single_best_uninsured)} р.: страховой лимит — "
                     f"{money(insurance_limit)} р. на банк.")
    else:
        notes.append("Вся сумма с процентами в пределах страхового лимита.")
    if params.personal_limit is not None and params.amount > params.personal_limit:
        notes.append(f"Сумма больше вашей предельной суммы в один банк "
                     f"({money(params.personal_limit)} р.).")
    diff = result.single_best_diff
    if diff > 0:
        comparison = f"распределение даёт на {money(diff)} р. больше"
    elif diff == 0:
        comparison = "столько же, сколько распределение"
    elif result.single_best_breaks_limits and params.personal_limit is not None:
        comparison = (f"на {money(-diff)} р. больше распределения, но сумма в одном банке больше "
                      f"вашей предельной")
    elif result.single_best_breaks_limits:
        comparison = f"на {money(-diff)} р. больше распределения, но часть суммы не застрахована"
    else:
        comparison = f"на {money(-diff)} р. больше распределения (погрешность шага дискретизации)"
    return dict(title=f"Если положить всё на один вклад: «{d.name}» ({d.bank_name}, "
                      f"{d.term_months} мес.)",
                income=money(result.single_best_income), notes=notes, comparison=comparison)


def text_report(params: CalcParams, result: CalcResult, bank_names, insurance_limit: int,
                avg_max_rate) -> str:
    """Текстовый отчёт с результатами расчёта (п. 4.1.1.1, функция 18)."""
    sp = lambda s: s.replace(NBSP, " ")
    lines = [
        "Программная система расчёта доходности и оптимизации распределения средств",
        "по банковским вкладам — результаты расчёта",
        f"Дата расчёта: {params.calc_date:%d.%m.%Y}; сформировано {clock.now():%d.%m.%Y %H:%M}",
        "Результаты носят рекомендательный характер.",
        "",
        "ПАРАМЕТРЫ",
        f"  Сумма размещения: {sp(money(params.amount))} р.",
        f"  Срок размещения: от {params.term_from} до {params.term_to} мес.",
        f"  Исключённые банки: {', '.join(bank_names[b] for b in sorted(params.excluded_bank_ids)) or 'нет'}",
        f"  Предельное число банков: {params.max_banks}",
        f"  Учёт предельного размера страхового возмещения ({sp(money(insurance_limit))} р.): "
        + (f"{MODE_TITLES[params.insurance_mode]} — {MODE_HINTS[params.insurance_mode]}"
           if params.personal_limit is None else
           "не применяется — задана предельная сумма в один банк"),
        f"  Предельная сумма в один банк: "
        f"{sp(money(params.personal_limit)) + ' р.' if params.personal_limit else 'не задана'}",
        f"  Ранее полученные проценты по вкладам и счетам в текущем году: "
        f"{sp(money(params.other_income))} р.",
        "",
        "РАСПРЕДЕЛЕНИЕ СРЕДСТВ (точный метод)",
    ]
    header = (f"  {'Банк':<28} {'Вклад':<22} {'Сумма, р.':>16} {'Срок':>5} {'Ставка':>7} "
              f"{'Капит.':>7} {'Доход, р.':>14}")
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for p in result.exact.positions:
        mark = " !" if is_suspicious(p.deposit.rate, avg_max_rate) else ""
        lines.append(f"  {p.deposit.bank_name[:28]:<28} {p.deposit.name[:22]:<22} "
                     f"{sp(money(p.amount)):>16} {p.deposit.term_months:>5} "
                     f"{rate(p.deposit.rate) + mark:>7} {('да' if p.deposit.capitalization else 'нет'):>7} "
                     f"{sp(money(p.income)):>14}")
    flagged = [p for p in result.exact.positions if is_suspicious(p.deposit.rate, avg_max_rate)]
    if flagged:
        lines.append("")
        lines.append("  ВНИМАНИЕ (отмечено «!»): нетипично высокая ставка, сверьте условия на сайте банка: "
                     + "; ".join(f"«{p.deposit.name}», {p.deposit.bank_name} — {rate(p.deposit.rate)} %"
                                 for p in flagged))
    lines += [
        "",
        "ИТОГОВЫЕ ПОКАЗАТЕЛИ",
        f"  Суммарный доход до налогообложения: {sp(money(result.exact.income))} р.",
        f"  Ориентировочный НДФЛ на данное распределение вкладов (см. оговорку ниже): "
        f"{sp(money(result.tax_total))} р.",
        f"  Суммарный доход после налогообложения (за вычетом НДФЛ): "
        f"{sp(money(result.income_after_tax))} р.",
        f"  Застраховано на конец срока (вклады с процентами): {sp(money(result.insured))} р.; "
        f"не застраховано: {sp(money(result.uninsured))} р.",
        f"  Нераспределённый остаток: {sp(money(result.remainder))} р."
        + (f" — {result.remainder_reasons[0]}" if result.remainder_reasons else ""),
    ]
    for extra in result.remainder_reasons[1:]:
        lines.append(f"      также действует: {extra}")
    if result.remainder_tips:
        lines.append("      что можно сделать:")
        lines += [f"        — {t}" for t in result.remainder_tips]
    single = single_deposit_summary(result, params, insurance_limit)
    lines.append(f"  {single['title']}: " + (f"{sp(single['income'])} р." if single['income'] else "—"))
    lines += [f"      {sp(n)}" for n in single["notes"]]
    if single["comparison"]:
        lines.append(f"      {sp(single['comparison'])}")
    lines.append(f"  Использовано банков: {result.exact.banks_used} из предельных {result.max_banks}")
    if result.k_plus_one_gain is not None:
        lines.append(f"  Прирост дохода при увеличении предельного числа банков на 1: "
                     f"{sp(money(result.k_plus_one_gain))} р.")
    lines += ["", "НДФЛ ПО ГОДАМ — ОРИЕНТИРОВОЧНАЯ ОЦЕНКА, НЕ РАСЧЁТ НАЛОГА (суммы в рублях)"]
    rows = [("Доход по вкладам, р.", lambda t: money(t.deposit_income)),
            ("Ранее полученные проценты, р.", lambda t: money(t.other_income)),
            ("Необлагаемая сумма, р.", lambda t: money(t.non_taxable)),
            ("Перенос прошлых лет, р.", lambda t: money(t.carried)),
            ("Налоговая база, р.", lambda t: money(max(t.base, 0))),   # база не бывает отрицательной
            ("Ставка налога", lambda t: t.scale),
            ("Налог без вкладов, р.", lambda t: money(t.tax_without)),
            ("Налог с вкладами, р.", lambda t: money(t.tax_full)),
            ("НДФЛ на данное распределение, р.", lambda t: money(t.tax))]
    lines.append(f"  {'Показатель':<34}" + "".join(f"{t.year:>16}" for t in result.tax_years))
    lines.append("  " + "-" * (34 + 16 * len(result.tax_years)))
    for title, value in rows:
        lines.append(f"  {title:<34}" + "".join(f"{sp(value(t)):>16}" for t in result.tax_years))
    lines.append(f"  Итого НДФЛ на данное распределение: {sp(money(result.tax_total))} р.")
    lines += [
        "",
        "  Система не выполняет функции налогового агента и не гарантирует соответствия оценки",
        "  налогу, который будет фактически начислен. Не учитываются: налоговый статус, иные доходы",
        "  и налоговые вычеты, счета, открытые совместно или на детей, фактические даты выплаты",
        "  процентов, досрочное расторжение вкладов, последующие изменения законодательства.",
        "  Порог 2 400 000 р. установлен для совокупности баз, включающей также доходы от дивидендов,",
        "  продажи имущества и ценных бумаг, цифровой валюты; такие доходы не учитываются.",
        "  Необлагаемая сумма равна 1 000 000 р. × наибольшая ключевая ставка ЦБ РФ на 1-е число",
        "  месяца текущего года по значениям, известным на дату расчёта; её последующие изменения",
        "  не учитываются.",
        "  Ставка 13 % применяется к совокупному процентному доходу текущего года, уменьшенному",
        "  на необлагаемую сумму, в пределах 2,4 млн р.; к части свыше 2,4 млн р. применяется",
        "  ставка НДФЛ 15 %.",
        "  Проценты вкладов с капитализацией отнесены к годам начисления, без капитализации —",
        "  к году окончания срока.",
        "  Для вкладов без капитализации дольше 15 месяцев учтён перенос неиспользованных",
        "  необлагаемых сумм всех лет срока.",
        "  Показан НДФЛ, приходящийся на доход по вкладам этого распределения, а не весь налог за год.",
        "  Величину налога определяет налоговый орган.",
    ]
    for n in result.notices:
        lines.append("Примечание: " + n)
    return "\n".join(lines) + "\n"
