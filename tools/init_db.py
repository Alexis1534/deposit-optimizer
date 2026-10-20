"""Создание файла базы данных из database/schema.sql и database/seed_demo.sql.

Запуск из корня проекта:
    python tools/init_db.py           — создать database/deposits.db, если его нет
    python tools/init_db.py --force   — пересоздать (перед этим сделайте копию файла, п. 4.3.2)
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(ROOT, "database")
DEFAULT_DB = os.path.join(DB_DIR, "deposits.db")


def create_schema(con):
    """Создаёт таблицы в открытом соединении."""
    with open(os.path.join(DB_DIR, "schema.sql"), encoding="utf-8") as f:
        con.executescript(f.read())


def build(db_path=DEFAULT_DB, with_seed=True):
    """Создаёт файл БД заново; при with_seed загружает демонстрационный справочник."""
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    create_schema(con)
    if with_seed:
        with open(os.path.join(DB_DIR, "seed_demo.sql"), encoding="utf-8") as f:
            con.executescript(f.read())
    con.commit()
    con.close()


if __name__ == "__main__":
    if os.path.exists(DEFAULT_DB) and "--force" not in sys.argv:
        print("Файл уже существует:", DEFAULT_DB, "(для пересоздания: --force)")
    else:
        build(DEFAULT_DB)
        print("Создан файл базы данных:", DEFAULT_DB)
