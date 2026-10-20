"""Подсистема пользовательского интерфейса — координатор вызовов (карта Константайна).

Интерфейс вызывает по отдельности:
  * управление данными — справочник и отобранные вклады;
  * внешние источники — сведения ЦБ;
  * оптимизацию — передавая ей отобранные вклады и сведения ЦБ параметрами.
Данные сеанса (последние введённые параметры и результат) хранятся только в оперативной
памяти (п. 4.1.2.12): при переходе на страницу справки и обратно они не теряются.
"""
import secrets
from datetime import date, timedelta

from flask import Flask, make_response, render_template, request

from app.data_management import repository
from app.data_management.repository import is_suspicious
from app.external_sources import cbr_client
from app.optimization import service
from app.ui import report, validation
from app.ui.report import MODE_HINTS, MODE_TITLES

STALE_DAYS = 30
SESSION_COOKIE = "calc_session"


def create_app(db_path: str, online: bool = True, today_fn=date.today) -> Flask:
    app = Flask(__name__)
    today = today_fn()

    # При запуске: контроль целостности справочника (п. 4.2.3) и сведения ЦБ (п. 4.2.5)
    catalog = repository.load_catalog(db_path)
    cbr = cbr_client.get_cbr_info(db_path, today, online=online)
    # токен сеанса → последнее состояние страницы расчёта: введённые значения, ошибки,
    # сообщения, параметры и результат; только в оперативной памяти
    sessions = {}

    bank_names = {b: n for b, n in catalog.banks}

    def context(state, errors=None, notices=None, result=None, params=None, message=None):
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
            sessions.pop(token, None)
        entry = sessions.get(token)
        if entry is None:
            return render_template("index.html", **context(validation.FormState()))
        return render_template("index.html", **context(**entry))

    def remember(page: dict):
        """Сохраняет состояние страницы расчёта в данных сеанса и показывает её."""
        token = request.cookies.get(SESSION_COOKIE) or secrets.token_hex(16)
        sessions[token] = page
        resp = make_response(render_template("index.html", **context(**page)))
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Strict")
        return resp

    @app.post("/calculate")
    def calculate():
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

        result = service.optimize(selected, params, cbr, catalog.insurance_limit)
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
                                  cbr.avg_max_rate)
        resp = make_response(text.encode("utf-8"))
        resp.headers["Content-Type"] = "text/plain; charset=utf-8"
        resp.headers["Content-Disposition"] = \
            f'attachment; filename="raschet_{params.calc_date:%Y%m%d}.txt"'
        return resp

    @app.get("/help")
    def help_page():
        return render_template("help.html", catalog=catalog, cbr=cbr,
                               fmt_money=report.money, fmt_rate=report.rate)

    return app
