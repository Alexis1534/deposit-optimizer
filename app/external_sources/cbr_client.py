"""Подсистема взаимодействия с внешними источниками (п. 4.1.1.5 ТЗ).

Получает у Банка России:
  * ключевую ставку — SOAP-сервис DailyInfoWebServ, метод KeyRate (текущее значение и
    наибольшее за год на 1-е число месяца — для необлагаемой суммы НДФЛ);
  * среднюю максимальную ставку топ-10 банков — страница cbr.ru/statistics/avgprocstav
    (используется подсистемой управления данными для проверки правдоподобия ставок
    справочника, для расчёта налога не применяется).
Полученные значения сохраняются в БД; при недоступности сети используются последние
сохранённые с сообщением пользователю (п. 4.2.5). Только стандартная библиотека.
"""
import re
import sqlite3
import urllib.request
from datetime import date
from typing import Optional, Tuple

from app import clock
from app.models import CbrInfo

SOAP_URL = "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx"
AVG_MAX_URL = "https://www.cbr.ru/statistics/avgprocstav/"
TIMEOUT_S = 5

_SOAP_BODY = """<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
 <soap12:Body>
  <KeyRate xmlns="http://web.cbr.ru/">
   <fromDate>{start}T00:00:00</fromDate>
   <ToDate>{end}T00:00:00</ToDate>
  </KeyRate>
 </soap12:Body>
</soap12:Envelope>"""


def _http(url: str, data: Optional[bytes] = None, headers=None) -> str:
    req = urllib.request.Request(url, data=data, headers=headers or {})
    req.add_header("User-Agent", "Mozilla/5.0 (deposit-optimizer coursework prototype)")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_key_rates(today: date) -> Tuple[float, str, float]:
    """(текущая ставка, дата её значения, наибольшая за год).

    Наибольшая ставка определяется по значениям на 1-е число каждого месяца налогового
    периода — именно так необлагаемая сумма считается по пункту 1 статьи 214.2 НК РФ.
    Пик внутри месяца, не попавший на 1-е число, в расчёт не идёт.
    """
    # запрос с 1 декабря прошлого года: сервис отдаёт значения только за рабочие дни, а 1 января
    # и другие первые числа, выпавшие на выходные, в ответе отсутствуют — ставку на такое число
    # даёт последнее значение до него (в том числе декабрьское для 1 января)
    body = _SOAP_BODY.format(start=date(today.year - 1, 12, 1).isoformat(), end=today.isoformat())
    xml = _http(SOAP_URL, body.encode("utf-8"),
                {"Content-Type": "application/soap+xml; charset=utf-8"})
    points = parse_key_rates(xml)
    last_dt, last_rate = points[-1]
    return last_rate, last_dt.strftime("%d.%m.%Y"), year_max_on_month_starts(points, today)


def parse_key_rates(xml: str):
    """Ряд значений ключевой ставки из ответа сервиса ЦБ: [(дата, ставка)] по возрастанию даты."""
    points = re.findall(r"<DT>(\d{4}-\d{2}-\d{2})[^<]*</DT>\s*<Rate>([\d.]+)</Rate>", xml)
    if not points:
        raise ValueError("ответ сервиса ЦБ не содержит значений ключевой ставки")
    return sorted((date.fromisoformat(d), float(r)) for d, r in points)


def year_max_on_month_starts(points, today: date) -> float:
    """Наибольшая ставка на 1-е число месяцев текущего года, уже наступивших на дату расчёта.

    Ставка на 1-е число — последнее значение ряда с датой не позже этого числа
    (значение действует до следующего изменения). Если для какого-то 1-го числа значений
    раньше него в ряду нет, берётся первое значение ряда.
    """
    rates = []
    for month in range(1, today.month + 1):
        first = date(today.year, month, 1)
        known = [r for d, r in points if d <= first]
        rates.append(known[-1] if known else points[0][1])
    return max(rates)


def fetch_avg_max_rate() -> Tuple[float, str]:
    """(средняя максимальная ставка, декада) — первая строка таблицы на странице ЦБ."""
    html = _http(AVG_MAX_URL)
    m = re.search(r"<td[^>]*>\s*([IV]{1,3}\.\d{2}\.\d{4})\s*</td>\s*<td[^>]*>\s*([\d\s]+,\d+)\s*</td>",
                  html)
    if not m:
        raise ValueError("на странице ЦБ не найдена таблица средней максимальной ставки")
    return float(m.group(2).replace(" ", "").replace(",", ".")), m.group(1)


def _read_cache(db_path: str) -> CbrInfo:
    """Последние сохранённые сведения (п. 4.1.2.2). Средней максимальной ставки может не быть."""
    con = sqlite3.connect(db_path)
    try:
        vals = {n: (v, a, f) for n, v, a, f in con.execute(
            "SELECT name, value, as_of, fetched_at FROM cbr_value")}
        year_max = dict(con.execute("SELECT year, max_rate FROM key_rate_year_max"))
    finally:
        con.close()
    kr = vals.get("key_rate_current")
    am = vals.get("avg_max_rate")
    if kr is None:
        raise RuntimeError("в базе данных нет сохранённого значения ключевой ставки ЦБ РФ")
    return CbrInfo(kr[0], kr[1], year_max,
                   am[0] if am else None, am[1] if am else "",
                   from_cache=True, message=f"получены {kr[2][:10]}")


def _write_cache(db_path: str, info: CbrInfo, today: date, key_rate_fresh: bool, avg_fresh: bool):
    stamp = clock.now().isoformat(timespec="seconds")
    con = sqlite3.connect(db_path)
    try:
        with con:   # одна транзакция: при сбое кеш остаётся прежним (п. 4.2.6)
            if key_rate_fresh:
                con.execute("INSERT OR REPLACE INTO cbr_value VALUES ('key_rate_current', ?, ?, ?)",
                            (info.key_rate_current, info.key_rate_as_of, stamp))
                con.execute("INSERT OR REPLACE INTO key_rate_year_max VALUES (?, ?, ?)",
                            (today.year, info.key_rate_year_max[today.year], stamp))
            if avg_fresh:
                con.execute("INSERT OR REPLACE INTO cbr_value VALUES ('avg_max_rate', ?, ?, ?)",
                            (info.avg_max_rate, info.avg_max_as_of, stamp))
    finally:
        con.close()


def get_cbr_info(db_path: str, today: date, online: bool = True) -> CbrInfo:
    """Сведения ЦБ: из сети с сохранением, иначе — последние сохранённые с сообщением (п. 4.2.5).

    Ключевая ставка и средняя максимальная ставка получаются независимо: недоступность
    одного источника не мешает обновить другое значение.
    """
    info = _read_cache(db_path)
    notes = []
    key_fresh = avg_fresh = False
    if online:
        try:
            kr, kr_as_of, year_max = fetch_key_rates(today)
            info.key_rate_current, info.key_rate_as_of = kr, kr_as_of
            info.key_rate_year_max = dict(info.key_rate_year_max)
            info.key_rate_year_max[today.year] = year_max
            key_fresh = True
        except Exception as exc:  # сеть недоступна, формат ответа изменился и т. п.
            notes.append(f"ключевая ставка не получена ({type(exc).__name__})")
        try:
            info.avg_max_rate, info.avg_max_as_of = fetch_avg_max_rate()
            avg_fresh = True
        except Exception as exc:
            notes.append(f"средняя максимальная ставка не получена ({type(exc).__name__})")

    if key_fresh and avg_fresh:
        info.from_cache, info.message = False, "Сведения ЦБ РФ получены."
    elif online:
        info.message = ("Источник сведений ЦБ РФ недоступен: " + "; ".join(notes) +
                        f". Используются последние сохранённые значения ({info.message}).")
    else:
        info.message = "Сведения ЦБ РФ взяты из сохранённых (" + info.message + ")."
    if info.avg_max_rate is None:
        info.message += (" Средняя максимальная ставка крупнейших банков отсутствует — проверка "
                         "правдоподобия ставок не выполняется.")

    if key_fresh or avg_fresh:
        try:
            _write_cache(db_path, info, today, key_fresh, avg_fresh)
        except sqlite3.Error:
            info.message += " Сохранить полученные сведения в базу данных не удалось."
    return info
