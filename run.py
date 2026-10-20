"""Средство запуска Системы (п. 4.6.1).

    python run.py                    — запуск; интерфейс: http://127.0.0.1:5000
    python run.py --offline          — не обращаться к сайту ЦБ РФ (сохранённые сведения)
    python run.py --db путь/к.db     — другой файл справочника
    python run.py --share            — демонстрационный режим: доступ с других устройств
                                       той же сети (Wi-Fi) по адресу, который выведет программа

По умолчанию сервер слушает только 127.0.0.1: доступ с других узлов сети не предусмотрен
(п. 4.4.1). Режим --share — отступление от этого пункта только для показа работы другим людям.
"""
import os
import socket
import sys
import webbrowser
from threading import Timer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from app.ui.routes import create_app  # noqa: E402

DB_PATH = os.path.join(ROOT, "database", "deposits.db")
HOST, PORT = "127.0.0.1", 5000


def lan_addresses():
    """Адреса компьютера в локальной сети — по ним другие устройства открывают Систему."""
    found = []
    try:  # адрес, через который компьютер выходит в сеть (пакеты при этом не отправляются)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.0.1", 9))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    return [a for i, a in enumerate(found) if not a.startswith("127.") and a not in found[:i]]


def main():
    db_path = DB_PATH
    if "--db" in sys.argv:
        db_path = os.path.abspath(sys.argv[sys.argv.index("--db") + 1])
    if not os.path.exists(db_path):
        print("Не найден файл базы данных:", db_path)
        print("Создайте его командой:  python tools/init_db.py")
        sys.exit(1)
    app = create_app(db_path, online="--offline" not in sys.argv)
    url = f"http://{HOST}:{PORT}/"
    host = HOST
    if "--share" in sys.argv:
        host = "0.0.0.0"
        print("ДЕМОНСТРАЦИОННЫЙ РЕЖИМ: Система доступна другим устройствам этой сети.")
        addrs = lan_addresses()
        if addrs:
            print("Адрес для других устройств (они должны быть в той же сети Wi-Fi):")
            for a in addrs:
                print(f"    http://{a}:{PORT}/")
        else:
            print("Не удалось определить адрес компьютера в сети — посмотрите его командой ipconfig.")
        print("Если Windows спросит про доступ к сети — разрешите для «Частных сетей».")
    print("Система запущена:", url, "(остановка — Ctrl+C)")
    if "--no-browser" not in sys.argv:
        Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
