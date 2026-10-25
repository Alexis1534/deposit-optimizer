"""Точка входа для размещения Системы на сервере (демонстрационный стенд вне п. 4.4.1 ТЗ).

Запуск:  waitress-serve --host=0.0.0.0 --port=8000 wsgi:app

Переменные окружения:
    DB_PATH      — файл справочника (по умолчанию database/deposits.db);
    CBR_OFFLINE  — 1: не обращаться к сайту ЦБ РФ, брать сохранённые сведения.
"""
import os

from app.ui.routes import create_app

ROOT = os.path.dirname(os.path.abspath(__file__))

app = create_app(os.environ.get("DB_PATH") or os.path.join(ROOT, "database", "deposits.db"),
                 online=os.environ.get("CBR_OFFLINE") != "1", public=True)
