"""Текущее время по Москве.

Дата расчёта, ключевая ставка ЦБ РФ и сроки вкладов отсчитываются по московскому времени,
поэтому оно не зависит от часового пояса компьютера или сервера (на хостинге обычно UTC).
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def now() -> datetime:
    return datetime.now(MOSCOW)


def today() -> date:
    return now().date()
