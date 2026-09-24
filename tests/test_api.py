"""End-to-end API tests: real HTTP server with a mocked upstream fetch."""

import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request

from hk_dashboard import eastmoney, yahoo
from hk_dashboard.server import DEFAULT_SYMBOLS, create_server
from tests.fakes import (
    FakeUpstream,
    canned_error_payload,
    make_chart_payload,
    make_em_kline_payload,
    make_em_quote_payload,
)


class ApiTestBase(unittest.TestCase):
    port = None

    def setUp(self):
        self.fake = FakeUpstream()
        self.static_dir = tempfile.mkdtemp(prefix="hk-static-")
        with open(os.path.join(self.static_dir, "index.html"), "w") as fh:
            fh.write("<html>fixture</html>")
        with open(os.path.join(self.static_dir, "app.js"), "w") as fh:
            fh.write("// fixture js")
        self.httpd = create_server(
            host="127.0.0.1", port=0, fetch_fn=self.fake,
            timeout=3.5, static_dir=self.static_dir, verbose_logs=False,
        )
        self.port = self.httpd.server_address[1]
        self.base = "http://127.0.0.1:%d" % self.port

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def get(self, path):
        """GET, returning (status, parsed_json_or_none)."""
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            status = exc.code
        else:
            status = resp.status
        try:
            return status, json.loads(body)
        except json.JSONDecodeError:
            return status, body.decode("utf-8", "replace")


class TestHealthAndDefaults(ApiTestBase):
    def test_health(self):
        status, data = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")

    def test_defaults(self):
        status, data = self.get("/api/defaults")
        self.assertEqual(status, 200)
        syms = [s["symbol"] for s in data["symbols"]]
        self.assertIn("0700.HK", syms)
        self.assertTrue(all(s.endswith(".HK") for s in syms))
        self.assertEqual(len(syms), len(DEFAULT_SYMBOLS))


class TestQuoteApi(ApiTestBase):
    def test_quote_ok_and_upstream_url(self):
        status, q = self.get("/api/quote?symbol=700")
        self.assertEqual(status, 200)
        self.assertEqual(q["symbol"], "0700.HK")
        self.assertEqual(self.fake.calls[-1]["url"],
                         yahoo.build_chart_url("0700.HK", "1d"))
        self.assertEqual(self.fake.calls[-1]["timeout"], 3.5)

    def test_quote_timeout_propagated(self):
        self.get("/api/quote?symbol=0700.HK")
        self.assertEqual(self.fake.calls[-1]["timeout"], 3.5)

    def test_quote_invalid_symbols(self):
        for bad in ("", "abc", "0700.US", "123456", "700..HK", "-700"):
            status, data = self.get("/api/quote?symbol=" + urllib.parse.quote(bad))
            self.assertEqual(status, 400, msg=repr(bad))
            self.assertIn("error", data, msg=repr(bad))

    def test_quote_upstream_not_found_is_404(self):
        self.fake.error = None
        self.fake.payload = canned_error_payload("No data found, symbol not found")
        status, data = self.get("/api/quote?symbol=0000.HK")
        self.assertEqual(status, 404)
        self.assertIn("error", data)

    def test_quote_upstream_failure_is_502(self):
        self.fake.payload = make_chart_payload()
        self.fake.error = yahoo.YahooHTTPError("Could not reach Yahoo Finance: timed out")
        status, data = self.get("/api/quote?symbol=0700.HK")
        self.assertEqual(status, 502)
        self.assertIn("error", data)


class TestHistoryApi(ApiTestBase):
    def test_history_ok_default_range(self):
        self.fake.payload = make_chart_payload(days=5)
        status, data = self.get("/api/history?symbol=9988")
        self.assertEqual(status, 200)
        self.assertEqual(data["symbol"], "9988.HK")
        self.assertEqual(data["range"], "6M")
        self.assertIn("range=6mo", self.fake.calls[-1]["url"])
        self.assertEqual(len(data["rows"]), 5)

    def test_history_all_ranges(self):
        for key, frag in (("1M", "1mo"), ("3M", "3mo"), ("6M", "6mo"),
                          ("1Y", "1y"), ("5Y", "5y")):
            status, data = self.get("/api/history?symbol=0700.HK&range=" + key)
            self.assertEqual(status, 200, msg=key)
            self.assertIn(frag, self.fake.calls[-1]["url"], msg=key)

    def test_history_invalid_range_is_400(self):
        for bad in ("2M", "max", "1d"):
            status, data = self.get("/api/history?symbol=0700.HK&range=" + bad)
            self.assertEqual(status, 400, msg=repr(bad))
            self.assertIn("error", data, msg=repr(bad))

    def test_history_empty_range_defaults_to_6m(self):
        self.fake.payload = make_chart_payload(days=2)
        status, data = self.get("/api/history?symbol=0700.HK&range=")
        self.assertEqual(status, 200)
        self.assertEqual(data["range"], "6M")

    def test_history_invalid_symbol_is_400(self):
        status, data = self.get("/api/history?symbol=abc&range=1M")
        self.assertEqual(status, 400)

    def test_history_upstream_not_found_is_404(self):
        self.fake.payload = canned_error_payload("No data found, symbol not found")
        status, data = self.get("/api/history?symbol=0000.HK&range=1Y")
        self.assertEqual(status, 404)


class TestStaticAndErrors(ApiTestBase):
    def test_index_served(self):
        try:
            with urllib.request.urlopen(self.base + "/", timeout=10) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn(b"fixture", resp.read())
        except urllib.error.HTTPError as exc:
            self.fail("GET / returned HTTP %s" % exc.code)

    def test_static_assets_served_via_static_prefix(self):
        try:
            with urllib.request.urlopen(self.base + "/static/app.js", timeout=10) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn(b"fixture js", resp.read())
            with urllib.request.urlopen(self.base + "/app.js", timeout=10) as resp:
                self.assertEqual(resp.status, 200)
        except urllib.error.HTTPError as exc:
            self.fail("static asset returned HTTP %s" % exc.code)

    def test_unknown_api_404(self):
        status, data = self.get("/api/nope")
        self.assertEqual(status, 404)

    def test_path_traversal_blocked(self):
        for path in ("/../tests/fakes.py", "/%2e%2e/tests/fakes.py", "/static/../../tests/fakes.py"):
            try:
                with urllib.request.urlopen(self.base + path, timeout=10) as resp:
                    body = resp.read()
            except urllib.error.HTTPError as exc:
                self.assertIn(exc.code, (400, 404), msg=path)
                continue
            self.assertNotIn(b"canned_error_payload", body, msg=path)




class TestEastMoneyFallbackViaApi(ApiTestBase):
    """End-to-end: Yahoo 429 on the server -> EastMoney fallback (mocked)."""

    def setUp(self):
        super().setUp()
        self.em = FakeUpstream(make_em_quote_payload())
        self.httpd = create_server(
            host="127.0.0.1", port=0, fetch_fn=self.fake,
            em_fetch_fn=self.em, timeout=3.5, static_dir=self.static_dir,
            verbose_logs=False,
        )
        self.port = self.httpd.server_address[1]
        self.base = "http://127.0.0.1:%d" % self.port

    def test_quote_falls_back_to_eastmoney_with_source(self):
        self.fake.error = yahoo.YahooHTTPError("Too Many Requests",
                                               status=429, retryable=True)
        status, q = self.get("/api/quote?symbol=0700.HK")
        self.assertEqual(status, 200)
        self.assertEqual(q["symbol"], "0700.HK")
        self.assertEqual(q["price"], 436.8)
        self.assertIn("EastMoney", q["source"])
        # Exactly one Yahoo leg (raised) and one EastMoney leg.
        self.assertEqual(len(self.em.calls), 1)
        self.assertEqual(self.em.calls[0]["url"],
                         eastmoney.build_quote_url("0700.HK"))
        self.assertEqual(self.em.calls[0]["timeout"], 3.5)

    def test_history_falls_back_to_eastmoney_kline(self):
        self.fake.error = yahoo.YahooHTTPError("Too Many Requests",
                                               status=429, retryable=True)
        self.em.payload = make_em_kline_payload(rows=6)
        status, h = self.get("/api/history?symbol=9988&range=1M")
        self.assertEqual(status, 200)
        self.assertEqual(h["symbol"], "9988.HK")
        self.assertEqual(h["range"], "1M")
        self.assertEqual(len(h["rows"]), 6)
        self.assertIn("EastMoney", h["source"])
        self.assertEqual(len(self.em.calls), 1)
        self.assertEqual(self.em.calls[0]["url"],
                         eastmoney.build_kline_url("9988.HK", "1M"))

    def test_yahoo_success_never_touches_eastmoney(self):
        status, q = self.get("/api/quote?symbol=0700.HK")
        self.assertEqual(status, 200)
        self.assertIn("Yahoo Finance", q["source"])
        self.assertEqual(len(self.em.calls), 0)

    def test_yahoo_not_found_stays_404_no_fallback(self):
        self.fake.payload = canned_error_payload("No data found, symbol not found")
        status, data = self.get("/api/quote?symbol=0000.HK")
        self.assertEqual(status, 404)
        self.assertEqual(len(self.em.calls), 0)

    def test_yahoo_403_not_retryable_stays_502_no_fallback(self):
        self.fake.error = yahoo.YahooHTTPError("Forbidden", status=403)
        status, data = self.get("/api/quote?symbol=0700.HK")
        self.assertEqual(status, 502)
        self.assertEqual(len(self.em.calls), 0)

    def test_eastmoney_not_found_is_404(self):
        self.fake.error = yahoo.YahooHTTPError("Too Many Requests",
                                               status=429, retryable=True)
        self.em.payload = {"rc": 1102, "rt": 4, "data": None}
        status, data = self.get("/api/quote?symbol=0000.HK")
        self.assertEqual(status, 404)
        self.assertIn("error", data)

    def test_both_providers_down_is_502(self):
        self.fake.error = yahoo.YahooHTTPError("Too Many Requests",
                                               status=429, retryable=True)
        self.em.error = eastmoney.EastMoneyError(
            "Could not reach EastMoney: conn refused", retryable=True)
        status, data = self.get("/api/quote?symbol=0700.HK")
        self.assertEqual(status, 502)
        self.assertIn("error", data)


class TestDefaultsRegression(unittest.TestCase):
    def test_hang_seng_bank_0011_hk_in_defaults(self):
        syms = [s["symbol"] for s in DEFAULT_SYMBOLS]
        self.assertIn("0011.HK", syms)
        hsbc = [s for s in DEFAULT_SYMBOLS if s["symbol"] == "0011.HK"]
        self.assertEqual(len(hsbc), 1)
        self.assertIn("Hang Seng Bank", hsbc[0]["name"])

    def test_tencent_is_first_default(self):
        self.assertEqual(DEFAULT_SYMBOLS[0]["symbol"], "0700.HK")


if __name__ == "__main__":
    unittest.main()
