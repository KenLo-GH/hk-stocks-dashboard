"""Tests for the Vercel FastAPI app (app.py) with fully mocked upstreams.

No external network is touched: the Yahoo and EastMoney ``fetch_json``
functions are replaced with the canned fakes from tests/fakes.py (both are
looked up at call time inside the provider modules, so patching the module
attributes is sufficient). Run with either:

    python3 -m pytest tests/test_vercel_app.py -v
    python3 -m unittest tests.test_vercel_app -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as vercel_app  # noqa: E402  (FastAPI app under test)
from fastapi.testclient import TestClient  # noqa: E402

from hk_dashboard import eastmoney, yahoo  # noqa: E402
from hk_dashboard.server import DEFAULT_SYMBOLS  # noqa: E402
from tests.fakes import (  # noqa: E402
    FakeUpstream,
    canned_error_payload,
    make_chart_payload,
    make_em_quote_payload,
)


class TestVercelAppBase(unittest.TestCase):
    """FastAPI TestClient with both upstream legs fully mocked."""

    def setUp(self):
        self.yfake = FakeUpstream(make_chart_payload())
        self.emfake = FakeUpstream(make_em_quote_payload())
        # Patch the upstream fetchers; the providers resolve them at call
        # time, so no other wiring is needed.
        self._orig_yahoo_fetch = yahoo.fetch_json
        self._orig_em_fetch = eastmoney.fetch_json
        yahoo.fetch_json = self.yfake
        eastmoney.fetch_json = self.emfake
        self.client = TestClient(vercel_app.app)

    def tearDown(self):
        yahoo.fetch_json = self._orig_yahoo_fetch
        eastmoney.fetch_json = self._orig_em_fetch


class TestRootAndStatic(TestVercelAppBase):
    def test_root_serves_index_html(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers.get("content-type", ""))
        self.assertTrue(len(r.text) > 100)

    def test_static_assets_served_via_static_prefix(self):
        for name in ("app.js", "style.css", "index.html"):
            r = self.client.get("/static/" + name)
            self.assertEqual(r.status_code, 200, msg=name)

    def test_static_missing_is_404(self):
        r = self.client.get("/static/does-not-exist.js")
        self.assertEqual(r.status_code, 404)

    def test_root_assets_also_served(self):
        # Parity with the stdlib server: /app.js resolves to static/app.js.
        r = self.client.get("/app.js")
        self.assertEqual(r.status_code, 200)


class TestHealthAndDefaults(TestVercelAppBase):
    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "hk-stocks-dashboard")
        self.assertIn("version", data)

    def test_defaults(self):
        r = self.client.get("/api/defaults")
        self.assertEqual(r.status_code, 200)
        syms = [s["symbol"] for s in r.json()["symbols"]]
        self.assertIn("0700.HK", syms)
        self.assertTrue(all(s.endswith(".HK") for s in syms))
        self.assertEqual(len(syms), len(DEFAULT_SYMBOLS))

    def test_unknown_api_404(self):
        r = self.client.get("/api/nope")
        self.assertEqual(r.status_code, 404)


class TestQuote(TestVercelAppBase):
    def test_quote_ok_yahoo_primary(self):
        r = self.client.get("/api/quote", params={"symbol": "700"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["symbol"], "0700.HK")
        self.assertEqual(len(self.yfake.calls), 1)
        self.assertEqual(self.yfake.calls[-1]["url"],
                         yahoo.build_chart_url("0700.HK", "1d"))

    def test_quote_falls_back_to_eastmoney_with_source(self):
        self.yfake.error = yahoo.YahooHTTPError("Too Many Requests",
                                                status=429, retryable=True)
        r = self.client.get("/api/quote", params={"symbol": "0700.HK"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["symbol"], "0700.HK")
        self.assertIn("EastMoney", data.get("source", ""))

    def test_quote_invalid_symbols_are_400(self):
        for bad in ("", "abc", "0700.US", "123456", "700..HK", "-700"):
            r = self.client.get("/api/quote", params={"symbol": bad})
            self.assertEqual(r.status_code, 400, msg=repr(bad))
            self.assertIn("error", r.json())

    def test_quote_missing_symbol_is_422(self):
        r = self.client.get("/api/quote")
        self.assertEqual(r.status_code, 422)

    def test_quote_upstream_not_found_is_404(self):
        self.yfake.payload = canned_error_payload("No data found, symbol not found")
        r = self.client.get("/api/quote", params={"symbol": "0000.HK"})
        self.assertEqual(r.status_code, 404)
        self.assertIn("error", r.json())

    def test_quote_upstream_failure_is_502(self):
        self.yfake.error = yahoo.YahooHTTPError(
            "Could not reach Yahoo Finance: timed out")
        self.emfake.error = eastmoney.EastMoneyError("EastMoney unavailable")
        r = self.client.get("/api/quote", params={"symbol": "0700.HK"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("error", r.json())


class TestHistory(TestVercelAppBase):
    def test_history_ok_default_range(self):
        self.yfake.payload = make_chart_payload(days=5)
        r = self.client.get("/api/history", params={"symbol": "9988"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["symbol"], "9988.HK")
        self.assertEqual(data["range"], "6M")
        self.assertEqual(len(data["rows"]), 5)

    def test_history_all_ranges(self):
        self.yfake.payload = make_chart_payload(days=3)
        for key, frag in (("1M", "1mo"), ("3M", "3mo"), ("6M", "6mo"),
                          ("1Y", "1y"), ("5Y", "5y")):
            r = self.client.get("/api/history",
                                params={"symbol": "0700.HK", "range": key})
            self.assertEqual(r.status_code, 200, msg=key)
            self.assertIn(frag, self.yfake.calls[-1]["url"], msg=key)

    def test_history_invalid_range_is_400(self):
        for bad in ("2M", "max", "1d"):
            r = self.client.get("/api/history",
                                params={"symbol": "0700.HK", "range": bad})
            self.assertEqual(r.status_code, 400, msg=repr(bad))
            self.assertIn("error", r.json())

    def test_history_invalid_symbol_is_400(self):
        r = self.client.get("/api/history",
                            params={"symbol": "abc", "range": "1M"})
        self.assertEqual(r.status_code, 400)

    def test_history_upstream_not_found_is_404(self):
        self.yfake.payload = canned_error_payload("No data found, symbol not found")
        r = self.client.get("/api/history",
                            params={"symbol": "0000.HK", "range": "1Y"})
        self.assertEqual(r.status_code, 404)

    def test_history_upstream_failure_is_502(self):
        self.yfake.error = yahoo.YahooHTTPError("boom", retryable=True)
        self.emfake.error = eastmoney.EastMoneyError("EastMoney unavailable")
        r = self.client.get("/api/history", params={"symbol": "0700.HK"})
        self.assertEqual(r.status_code, 502)


if __name__ == "__main__":
    unittest.main(verbosity=2)
