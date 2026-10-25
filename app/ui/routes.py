"""Подсистема пользовательского интерфейса — координатор вызовов (карта Константайна).

Интерфейс вызывает по отдельности:
  * управление данными — справочник и отобранные вклады;
  * внешние источники — сведения ЦБ;
  * оптимизацию — передавая ей отобранные вклады и сведения ЦБ параметрами.
Данные сеанса (последние введённые параметры и результат) хранятся только в оперативной
памяти (п. 4.1.2.12): при переходе на страницу справки и обратно они не теряются.

При размещении в интернете (public=True) дополнительно: учёт прокси хостинга, cookie
только по HTTPS, ограничение частоты расчётов с одного адреса.
"""
import secrets
import threading
import time
from collections import OrderedDict, defaultdict, deque
from datetime import timedelta

from flask import Flask, make_response, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from app import clock
from app.data_management import repository
from app.data_management.repository import is_suspicious
from app.external_sources import cbr_client
from app.optimization import service
from app.ui import report, validation
from app.ui.report import MODE_HINTS, MODE_TITLES

STALE_DAYS = 30
SESSION_COOKIE = "calc_session"
MAX_SESSIONS = 300              # больше — вытесняются самые давние
SESSION_TTL_S = 3 * 3600        # данные сеанса без обращений дольше 3 ч удаляются
CALC_LIMIT = (20, 60)           # не больше 20 расчётов за 60 с с одного адреса (public)
CONCURRENT_CALCS = 2            # одновременно выполняемых расчётов
BUSY_WAIT_S = 15


class SessionStore:
    """Данные сеансов в оперативной памяти с ограничением числа и срока хранения."""

    def __init__(self, limit=MAX_SESSIONS, ttl=SESSION_TTL_S):
        self.limit, self.ttl = limit, ttl
        self.items = OrderedDict()
        self.lock = threading.Lock()

    def get(self, token):
        with self.lock:
            item = self.items.get(token)
            if item is None:
                return None
            if time.monotonic() - item[0] > self.ttl:
                del self.items[token]
                return None
            self.items[token] = (time.monotonic(), item[1])
            self.items.move_to_end(token)
            return item[1]

    def put(self, token, page):
        with self.lock:
            self.items[token] = (time.monotonic(), page)
            self.items.move_to_end(token)
            now = time.monotonic()
            while self.items:
                oldest_token, (stamp, _) = next(iter(self.items.items()))
                if len(self.items) <= self.limit and now - stamp <= self.ttl:
                    break
                del self.items[oldest_token]

    def pop(self, token):
        with self.lock:
            self.items.pop(token, None)


def create_app(db_path: str, online: bool = True, today_fn=clock.today,
               public: bool = False) -> Flask:
    app = Flask(__name__)
    if public:      # адрес посетителя и протокол — из заголовков прокси хостинга
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # При запуске: контроль целостности справочника (п. 4.2.3) и сведения ЦБ (п. 4.2.5)
    catalog = repository.load_catalog(db_path)
    day = {"date": today_fn(), "cbr": None}
    day["cbr"] = cbr_client.get_cbr_info(db_path, day["date"], online=online)
    day_lock = threading.Lock()

    def current():
        """Дата расчёта и сведения ЦБ. Если система работает дольше суток, со сменой даты
        сведения ЦБ обновляются в фоне, а до их получения используются прежние."""
        today = today_fn()
        with day_lock:
            if today != day["date"]:
                day["date"] = today

                def refresh():
                    info = cbr_client.get_cbr_info(db_path, today, online=online)
                    with day_lock:
                        if day["date"] == today:
                            day["cbr"] = info
                threading.Thread(target=refresh, daemon=True).start()
            return day["date"], day["cbr"]

    # токен сеанса → последнее состояние страницы расчёта: введённые значения, ошибки,
    # сообщения, параметры и результат; только в оперативной памяти
    sessions = SessionStore()
    calc_slots = threading.BoundedSemaphore(CONCURRENT_CALCS)
    recent_calcs = defaultdict(deque)
    rate_lock = threading.Lock()

    def too_frequent() -> bool:
        if not public:
            return False
        count, window = CALC_LIMIT
        now = time.monotonic()
        with rate_lock:
            stamps = recent_calcs[request.remote_addr or "?"]
            while stamps and now - stamps[0] > window:
                stamps.popleft()
            if len(stamps) >= count:
                return True
            stamps.append(now)
            if len(recent_calcs) > 10_000:          # не копить адреса бесконечно
                for addr in [a for a, q in recent_calcs.items() if not q]:
                    del recent_calcs[addr]
            return False

    bank_names = {b: n for b, n in catalog.banks}

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    def context(state, errors=None, notices=None, result=None, params=None, message=None):
        today, cbr = current()
        stale = None
        if catalog.earliest_actual_date and \
                today - catalog.earliest_actual_date > timedelta(days=STALE_DAYS):
            stale = catalog.earliest_actual_date
        return dict(
            catalog=catalog, cbr=cbr, state=state, errors=errors or {},
            field_names=validation.FIELD_NAMES, notices=notices or [], result=result,
            params=params, message=message, stale_date=stale, today=today,
            modes=MODE_TITLES, mode_hints=MODE_HINTS, fmt_money=report.money, fmt_rate=report.rate,
            suspicious=lambda r: is_suspicious(r, cbr.avg_max_rate),
            suspicious_positions=[p for p in (result.exact.positions if result else [])
                                  if is_suspicious(p.deposit.rate, cbr.avg_max_rate)],
            bank_count=len(catalog.banks), deposit_count=len(catalog.deposits),
        )

    @app.get("/")
    def index():
        token = request.cookies.get(SESSION_COOKIE, "")
        if "reset" in request.args:                 # «Очистить форму»: новый расчёт с нуля
            sessions.pop(token)
        entry = sessions.get(token)
        if entry is None:
            return render_template("index.html", **context(validation.FormState()))
        return render_template("index.html", **context(**entry))

    def remember(page: dict):
        """Сохраняет состояние страницы расчёта в данных сеанса и показывает её."""
        token = request.cookies.get(SESSION_COOKIE) or secrets.token_hex(16)
        sessions.put(token, page)
        resp = make_response(render_template("index.html", **context(**page)))
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Strict", secure=public)
        return resp

    @app.post("/calculate")
    def calculate():
        today, cbr = current()
        state = validation.form_from_request(request.form)
        checked = validation.validate(state, [b for b, _ in catalog.banks], today)
        if checked.errors:
            return remember(dict(state=state, errors=checked.errors, notices=checked.notices))
        params = checked.params
        state.max_banks = str(params.max_banks)

        selected = repository.select_deposits(catalog.deposits, params.amount, params.term_from,
                                              params.term_to, params.excluded_bank_ids)
        if not selected:
            return remember(dict(
                state=state, notices=checked.notices,
                message="Нет вкладов, удовлетворяющих заданным параметрам: проверьте сумму, "
                        "границы срока и перечень исключённых банков."))

        if too_frequent():
            return remember(dict(state=state, notices=checked.notices,
                                 message="Слишком много расчётов подряд. Подождите минуту и "
                                         "повторите расчёт."))
        if not calc_slots.acquire(timeout=BUSY_WAIT_S):
            return remember(dict(state=state, notices=checked.notices,
                                 message="Сейчас выполняется много расчётов одновременно. "
                                         "Повторите расчёт через несколько секунд."))
        try:
            result = service.optimize(selected, params, cbr, catalog.insurance_limit)
        finally:
            calc_slots.release()
        return remember(dict(state=state, notices=checked.notices, result=result, params=params))

    @app.get("/save")
    def save():
        entry = sessions.get(request.cookies.get(SESSION_COOKIE, ""))
        if entry is None or entry.get("result") is None:
            return render_template("index.html", **context(
                entry["state"] if entry else validation.FormState(),
                message="Нет результата расчёта для сохранения — сначала выполните расчёт."))
        params, result = entry["params"], entry["result"]
        text = report.text_report(params, result, bank_names, catalog.insurance_limit,
                                  current()[1].avg_max_rate)
        resp = make_response(text.encode("utf-8"))
        resp.headers["Content-Type"] = "text/plain; charset=utf-8"
        resp.headers["Content-Disposition"] = \
            f'attachment; filename="raschet_{params.calc_date:%Y%m%d}.txt"'
        return resp

    @app.get("/help")
    def help_page():
        return render_template("help.html", catalog=catalog, cbr=current()[1],
                               fmt_money=report.money, fmt_rate=report.rate)

    return app
