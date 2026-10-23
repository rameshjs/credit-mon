"""Offline tests: canned provider payloads served from a local HTTP server.

Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from credit_mon import history, providers  # noqa: E402
from credit_mon.providers import DeepSeekProvider, ZaiProvider  # noqa: E402

DEEPSEEK_BALANCE = {
    "is_available": True,
    "balance_infos": [
        {"currency": "CNY", "total_balance": "110.00", "granted_balance": "10.00", "topped_up_balance": "100.00"},
        {"currency": "USD", "total_balance": "15.42", "granted_balance": "0.00", "topped_up_balance": "15.42"},
    ],
}

ZAI_ACCOUNT = {"code": 200, "success": True, "data": {
    "customerId": "71321768207710758", "availableBalance": 42.5, "balance": 45.0,
    "rechargeAmount": 200.0, "giveAmount": 15.0, "totalSpendAmount": 170.0, "frozenBalance": 2.5}}

ZAI_QUOTA = {"code": 200, "success": True, "data": {"limits": [
    {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "usage": 800000000,
     "currentValue": 127694464, "remaining": 672305536, "percentage": 15,
     "nextResetTime": 1770648402389},
    {"type": "TOKENS_LIMIT", "unit": 6, "number": 7, "usage": 800000000,
     "currentValue": 400000000, "remaining": 400000000, "percentage": 50},
    {"type": "TIME_LIMIT", "unit": 5, "number": 1, "usage": 4000, "currentValue": 1828, "percentage": 45},
]}}

ZAI_SUBS = {"code": 200, "success": True, "data": [
    {"productName": "GLM Coding Max", "status": "VALID",
     "valid": "2026-02-12 00:00:00-2026-03-12 00:00:00", "nextRenewTime": "2026-03-12"}]}

ZAI_AUTH_FAIL = {"code": 401, "success": False, "message": "当前用户不存在coding plan"}


class Handler(BaseHTTPRequestHandler):
    seen_auth: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        Handler.seen_auth.append(self.headers.get("Authorization"))
        path = self.path.split("?")[0]
        if path == "/user/balance":
            return self._send(200, DEEPSEEK_BALANCE)
        if path == ZaiProvider.PATH_ACCOUNT:
            # emulate the bare-key-only auth style of the account endpoint
            if self.headers.get("Authorization", "").startswith("Bearer "):
                return self._send(401, {"code": 401, "message": "invalid token"})
            return self._send(200, ZAI_ACCOUNT)
        if path == ZaiProvider.PATH_QUOTA:
            return self._send(200, ZAI_QUOTA)
        if path == ZaiProvider.PATH_SUBS:
            return self._send(200, ZAI_SUBS)
        if path == "/quota-only" + ZaiProvider.PATH_ACCOUNT:
            return self._send(200, ZAI_AUTH_FAIL)
        return self._send(404, {"error": "not found"})

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_deepseek_prefers_configured_currency(self):
        p = DeepSeekProvider()
        p.BASE = self.base
        r = p.fetch({"currency": "USD"}, "sk-test-12345")
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.currency, "USD")
        self.assertAlmostEqual(r.total, 15.42)
        self.assertEqual(r.headline, "$15.42")
        self.assertIn(("Topped up", "$15.42"), r.rows)

    def test_deepseek_defaults_to_first_currency(self):
        p = DeepSeekProvider()
        p.BASE = self.base
        r = p.fetch({"currency": None}, "sk-test-12345")
        self.assertEqual(r.currency, "CNY")
        self.assertEqual(r.headline, "¥110.00")

    def test_zai_balance_quota_plan_and_auth_fallback(self):
        p = ZaiProvider()
        p.BASES = {"intl": self.base}
        r = p.fetch({"region": "intl", "show_quota": True}, "zai-key-abc")
        self.assertTrue(r.ok, r.warnings)
        self.assertAlmostEqual(r.total, 42.5)
        self.assertEqual(r.currency, "USD")
        labels = dict(r.rows)
        self.assertEqual(labels["Available"], "$42.50")
        self.assertEqual(labels["Lifetime spend"], "$170.00")
        self.assertIn("15% used", labels["5h quota"])
        self.assertIn("50% used", labels["Weekly quota"])
        self.assertEqual(labels["Web tools"], "1,828/4,000 this month")
        self.assertIn("GLM Coding Max", labels["Plan"])
        # Bearer must have been tried and rejected before the bare-key retry worked
        self.assertTrue(any(a and a.startswith("Bearer ") for a in Handler.seen_auth))

    def test_zai_quota_only_account_still_renders(self):
        p = ZaiProvider()
        p.BASES = {"intl": self.base}
        p.PATH_ACCOUNT = "/quota-only" + ZaiProvider.PATH_ACCOUNT
        r = p.fetch({"region": "intl", "show_quota": True}, "zai-key-abc")
        self.assertTrue(r.ok)
        self.assertIsNone(r.currency)
        self.assertIn("%", r.headline)

    def test_zai_total_failure_raises(self):
        p = ZaiProvider()
        p.BASES = {"intl": self.base + "/nope"}
        p.PATH_ACCOUNT = "/missing"
        with self.assertRaises(providers.ProviderError):
            p.fetch({"region": "intl", "show_quota": False}, "k")

    def test_money_formatting(self):
        self.assertEqual(providers.fmt_money(3.5, "USD"), "$3.50")
        self.assertEqual(providers.fmt_money(0.0412, "USD"), "$0.041")
        self.assertEqual(providers.fmt_money(None, "USD"), "—")


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = f"/tmp/cm-test-{os.getpid()}"
        p = history.history_path()
        if p.exists():
            p.unlink()

    def tearDown(self):
        p = history.history_path()
        if p.exists():
            p.unlink()
        if self.tmp:
            os.environ["XDG_STATE_HOME"] = self.tmp
        else:
            os.environ.pop("XDG_STATE_HOME", None)

    def test_spend_today_ignores_topup(self):
        import time
        now = time.time()
        base = history._today_start() + 60
        if now - base < 600:  # just after midnight: skip, window too small
            return
        history.record("deepseek", 10.00, "USD", base)
        history.record("deepseek", 8.00, "USD", base + 120)   # spent 2.00
        history.record("deepseek", 28.00, "USD", base + 240)  # top-up, must not be negative spend
        history.record("deepseek", 27.00, "USD", base + 360)  # spent 1.00
        stats = history.stats("deepseek", 27.00)
        self.assertAlmostEqual(stats["spend_today"], 3.00, places=4)

    def test_no_history_is_none(self):
        stats = history.stats("zai", 5.0)
        self.assertIsNone(stats["spend_today"])
        self.assertIsNone(stats["days_left"])


if __name__ == "__main__":
    unittest.main()
