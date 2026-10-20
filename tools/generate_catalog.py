"""Генератор синтетического справочника с «сеточными» линейками вкладов.

Реальные банки публикуют не одну ставку на продукт, а сетку «срок → ставка»: у одного
продукта бывает до 13 сроков от 1 до 18 месяцев. В схеме Системы каждая пара «срок — ставка»
это отдельная запись таблицы «Вклад», поэтому один продукт даёт 5–13 строк справочника.

Запуск:
    python tools/generate_catalog.py 500                  — справочник на ~500 вкладов
    python tools/generate_catalog.py 500 --banks 20 --out database/big.db

Полученный файл можно открыть Системой: python run.py --db database/big.db
"""
import argparse
import os
import random
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from init_db import create_schema  # noqa: E402

# Сроки сетки: как в линейках крупных банков — чаще короткие, есть «длинные» ступени
GRID_TERMS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 15, 18]

PRODUCT_NAMES = ["Вклад", "Вклад плюс", "Промо", "Накопительный", "Пенсионный", "Онлайн",
                 "Максимум", "Новогодний", "Юбилейный", "Инвестиционный"]
# Банки вымышленные: название начинается со слова «Демобанк», номер лицензии имеет вид
# «Д-0001» и не совпадает с номером лицензии реального банка.
BANK_WORDS = ["Северный", "Приокский", "Уральский", "Балтийский", "Волжский",
              "Сибирский", "Донской", "Невский", "Камский", "Таврический"]


def rate_for_term(base: float, term: int, rnd: random.Random) -> float:
    """Ставка по сроку: пик на 3–6 месяцах, дальше спад — как в реальных сетках."""
    if term <= 2:
        shape = -0.8
    elif term <= 6:
        shape = 0.6
    elif term <= 12:
        shape = 0.0
    else:
        shape = -0.9
    return round(base + shape + rnd.uniform(-0.25, 0.25), 2)


def build(path, target_rows, banks_count, seed=2026):
    rnd = random.Random(seed)
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    create_schema(con)
    con.executescript("""
        INSERT INTO setting(key, value, description) VALUES
          ('insurance_limit', '1400000', 'Предельный размер страхового возмещения, р.');
        INSERT INTO cbr_value(name, value, as_of, fetched_at) VALUES
          ('key_rate_current', 14.00, '15.09.2026', '2026-09-16T00:00:00'),
          ('avg_max_rate', 12.948, 'I.09.2026', '2026-09-16T00:00:00');
        INSERT INTO key_rate_year_max(year, max_rate, fetched_at) VALUES
          (2026, 16.00, '2026-09-16T00:00:00');
    """)

    for i in range(banks_count):
        word = BANK_WORDS[i % len(BANK_WORDS)]
        suffix = "" if i < len(BANK_WORDS) else f"-{i // len(BANK_WORDS) + 1}"
        con.execute("INSERT INTO bank(bank_id, name, license_no) VALUES (?,?,?)",
                    (i + 1, f"Демобанк «{word}{suffix}»", f"Д-{i + 1:04d}"))

    rows, bank_id = [], 1
    while len(rows) < target_rows:
        # у банка 1–4 линейки; у части линеек полная сетка сроков, у части — короткая
        for _ in range(rnd.randint(1, 4)):
            promo = rnd.random() < 0.08          # изредка — промо-линейка с повышенной ставкой
            product = "Промо-вклад" if promo else rnd.choice(PRODUCT_NAMES)
            base = rnd.uniform(16.0, 17.5) if promo else rnd.uniform(10.5, 15.0)
            terms = GRID_TERMS if rnd.random() < 0.55 else rnd.sample(GRID_TERMS, rnd.randint(3, 7))
            min_sum = rnd.choice([1000, 10000, 30000, 50000, 100000, 500000])
            max_sum = rnd.choice([None, None, None, min_sum * 20, min_sum * 100, 5_000_000])
            cap = rnd.random() < 0.4
            actual = f"2026-09-{rnd.randint(1, 16):02d}"
            for term in sorted(terms):
                rows.append((bank_id, f"{product} {term} мес.", rate_for_term(base, term, rnd),
                             term, min_sum, max_sum, int(cap), actual))
                if len(rows) >= target_rows:
                    break
            if len(rows) >= target_rows:
                break
        bank_id = bank_id % banks_count + 1

    con.executemany("INSERT INTO deposit(bank_id, name, rate, term_months, min_sum, max_sum, "
                    "capitalization, actual_date) VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Синтетический справочник с сеточными линейками")
    ap.add_argument("rows", type=int, help="сколько записей о вкладах создать")
    ap.add_argument("--banks", type=int, default=20)
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()
    out = args.out or os.path.join(ROOT, "database", f"catalog_{args.rows}.db")
    n = build(out, args.rows, args.banks, args.seed)
    print(f"Создан справочник: {out} — банков {args.banks}, вкладов {n}")
