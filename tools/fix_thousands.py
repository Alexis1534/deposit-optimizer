"""Одноразовая правка справочника: в названиях вкладов «50к» -> «50 тыс.».

Запуск:  python tools/fix_thousands.py [путь к базе]   (по умолчанию database/deposits.db)
"""
import re
import sqlite3
import sys

PATTERN = re.compile(r"(?<=\d)\s?[кКkK](?![\w])")


def main(path: str) -> None:
    conn = sqlite3.connect(path)
    changed = 0
    for dep_id, name in conn.execute("SELECT deposit_id, name FROM deposit").fetchall():
        new = PATTERN.sub(" тыс.", name or "")
        if new != name:
            conn.execute("UPDATE deposit SET name = ? WHERE deposit_id = ?", (new, dep_id))
            changed += 1
    conn.commit()
    conn.close()
    print(f"Исправлено названий вкладов: {changed}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "database/deposits.db")
