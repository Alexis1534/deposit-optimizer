#!/usr/bin/env sh
# Запуск на Linux/macOS: ./run_unix.sh   (первый запуск создаёт окружение .venv)
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Первый запуск: создаю виртуальное окружение и устанавливаю библиотеки..."
  python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt || exit 1
fi
[ -f database/deposits.db ] || .venv/bin/python tools/init_db.py
exec .venv/bin/python run.py "$@"
