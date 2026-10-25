"""Размещение на сервере: данные сеансов, ограничение частоты расчётов, смена даты."""
import time
import unittest
from datetime import date, timedelta
from unittest import mock

from helpers import demo_db
from app.ui import routes
from app.ui.routes import SessionStore, create_app

FORM = {"amount": "300000", "term_from": "1", "term_to": "18", "insurance_mode": "2", "other_income": "0"}


class Sessions(unittest.TestCase):
    def test_limit_evicts_oldest(self):
        store = SessionStore(limit=3, ttl=3600)
        for i in range(5):
            store.put(f"t{i}", {"n": i})
        self.assertIsNone(store.get("t0"))
        self.assertIsNone(store.get("t1"))
        self.assertEqual(store.get("t4"), {"n": 4})
        self.assertEqual(len(store.items), 3)

    def test_expired_session_is_dropped(self):
        store = SessionStore(limit=10, ttl=0.05)
        store.put("a", {"n": 1})
        time.sleep(0.1)
        self.assertIsNone(store.get("a"))

    def test_recently_used_survives(self):
        store = SessionStore(limit=2, ttl=3600)
        store.put("a", 1)
        store.put("b", 2)
        store.get("a")
        store.put("c", 3)
        self.assertEqual(store.get("a"), 1)
        self.assertIsNone(store.get("b"))


class PublicMode(unittest.TestCase):
    def client(self, public=True):
        return create_app(demo_db(), online=False, today_fn=lambda: date(2026, 9, 16),
                          public=public).test_client()

    def test_secure_cookie_and_headers(self):
        resp = self.client().post("/calculate", data=FORM)
        self.assertIn("Secure", resp.headers["Set-Cookie"])
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        local = self.client(public=False).post("/calculate", data=FORM)
        self.assertNotIn("Secure", local.headers["Set-Cookie"])

    def test_rate_limit_per_address(self):
        c = self.client()
        with mock.patch.object(routes, "CALC_LIMIT", (3, 60)):
            pages = [c.post("/calculate", data=FORM, environ_base={"REMOTE_ADDR": "1.2.3.4"})
                     .data.decode() for _ in range(4)]
            other = c.post("/calculate", data=FORM, environ_base={"REMOTE_ADDR": "5.6.7.8"}).data.decode()
        self.assertTrue(all("Слишком много расчётов" not in p for p in pages[:3]))
        self.assertIn("Слишком много расчётов", pages[3])
        self.assertNotIn("Слишком много расчётов", other)

    def test_no_rate_limit_locally(self):
        c = self.client(public=False)
        with mock.patch.object(routes, "CALC_LIMIT", (1, 60)):
            pages = [c.post("/calculate", data=FORM).data.decode() for _ in range(3)]
        self.assertTrue(all("Слишком много расчётов" not in p for p in pages))


class DateRollover(unittest.TestCase):
    def test_calc_date_follows_clock_and_cbr_refreshes(self):
        days = [date(2026, 9, 16)]
        calls = []
        real = routes.cbr_client.get_cbr_info

        def counting(db, today, online=True):
            calls.append(today)
            return real(db, today, online=online)

        with mock.patch.object(routes.cbr_client, "get_cbr_info", side_effect=counting):
            c = create_app(demo_db(), online=False, today_fn=lambda: days[0]).test_client()
            c.post("/calculate", data=FORM)
            days[0] += timedelta(days=1)
            c.post("/calculate", data=FORM)
            text = c.get("/save").data.decode()
            for _ in range(50):
                if len(calls) >= 2:
                    break
                time.sleep(0.02)
        self.assertIn("Дата расчёта: 17.09.2026", text)
        self.assertEqual(calls, [date(2026, 9, 16), date(2026, 9, 17)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
