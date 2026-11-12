-- Схема базы данных: банки, вклады, настройки и кеш сведений ЦБ.
-- Параметры расчёта и результат в БД не хранятся (см. app/models.py).
-- Справочник содержит только банки - участники системы страхования вкладов.
--
-- CHECK-ограничений нет намеренно: справочник правится внешними средствами
-- (DB Browser for SQLite), а некорректные записи находит проверка при запуске
-- и показывает пользователю, а не отвергает молча при вводе.

CREATE TABLE IF NOT EXISTS bank (
    bank_id        INTEGER PRIMARY KEY,              -- # Код банка (присваивается автоматически)
    name           TEXT,                             -- * Наименование банка
    license_no     TEXT                              -- * Номер лицензии ЦБ РФ (заполнен, уникален)
);

CREATE TABLE IF NOT EXISTS deposit (
    deposit_id     INTEGER PRIMARY KEY,              -- # Код вклада
    bank_id        INTEGER REFERENCES bank(bank_id), -- * Код банка, предлагающего вклад
    name           TEXT,                             -- * Наименование вклада
    rate           REAL,                             -- * Процентная ставка, % годовых
    term_months    INTEGER,                          -- * Срок размещения, мес.
    min_sum        INTEGER,                          -- * Минимальная сумма, р.
    max_sum        INTEGER,                          -- o Максимальная сумма, р. (NULL — без ограничения)
    capitalization INTEGER NOT NULL DEFAULT 0,       -- * Признак капитализации (1/0)
    actual_date    TEXT                              -- * Дата актуальности сведений, ГГГГ-ММ-ДД
);

-- Постоянные величины (предельный размер страхового возмещения)
CREATE TABLE IF NOT EXISTS setting (
    key            TEXT PRIMARY KEY,
    value          TEXT NOT NULL,
    description    TEXT
);

-- Последние полученные сведения ЦБ РФ
CREATE TABLE IF NOT EXISTS cbr_value (
    name           TEXT PRIMARY KEY,                 -- key_rate_current | avg_max_rate
    value          REAL NOT NULL,                    -- % годовых
    as_of          TEXT NOT NULL,                    -- дата или декада, к которой относится значение
    fetched_at     TEXT NOT NULL                     -- момент получения
);

-- Наибольшее значение ключевой ставки за календарный год (необлагаемая сумма НДФЛ)
CREATE TABLE IF NOT EXISTS key_rate_year_max (
    year           INTEGER PRIMARY KEY,
    max_rate       REAL NOT NULL,
    fetched_at     TEXT NOT NULL
);
