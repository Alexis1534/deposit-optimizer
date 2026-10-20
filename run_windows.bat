@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Первый запуск: создаю виртуальное окружение и устанавливаю библиотеки...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
if not exist database\deposits.db .venv\Scripts\python tools\init_db.py
.venv\Scripts\python run.py %*
pause
