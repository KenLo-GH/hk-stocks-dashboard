"""Unit tests for the Yahoo Finance client with mocked upstream data."""

import datetime as dt
import io
import json
import unittest
from unittest import mock

from hk_dashboard import yahoo
from hk_dashboard.symbols import SymbolError
from tests.fakes import FakeUpstream, canned_error_payload, make_chart_payload


class TestBuildChartUrl(unittest.TestCase):
    def test_url_format(self):
        url = yahoo.build_chart_url("0700.HK", "6mo")
        self.assertEqual(
            url,
            "https://query1.finance.yahoo.com/v8/finance/chart/0700.HK"
            "?range=6mo&interval=1d&events=div%2Csplit",
        )

    def test_rejects_unnormalized_symbol(self):
        with self.assertRaises(SymbolError):
            yahoo.build_chart_url("700", "6mo")

    def test_rejects_injection_range_values(self):
        for bad in ("", "1mo;rm", "1mo&interval=1d", "1mo%00", "1 mo"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                yahoo.build_chart_url("0700.HK", bad)

    def test_accepts_valid_upstream_range_values(self):
        # build_chart_url trusts the whitelist in get_history; any pure
        # alphanumeric upstream value is a valid contract input.
        for good in ("1mo", "3mo", "6mo", "1y", "5y", "1d"):
            self.assertIn("range=" + good, yahoo.build_chart_url("0700.HK", good))


class TestGetQuote(unittest.TestCase):
    def test_happy_path(self):
        fake = FakeUpstream(make_chart_payload(price=400.6, prev_close=396.0,
                                               last_ts=1768435200))
        q = yahoo.get_quote("0700.HK", fetch_fn=fake, timeout=3.0)
        self.assertEqual(q["symbol"], "0700.HK")
        self.assertEqual(q["name"], "Tencent Holdings Ltd.")
        self.assertEqual(q["price"], 400.6)
        self.assertEqual(q["previousClose"], 396.0)
        self.assertAlmostEqual(q["change"], 4.6)
        self.assertAlmostEqual(q["pctChange"], 1.16)
        self.assertEqual(q["currency"], "HKD")
        self.assertIn(q["marketState"], ("open", "closed"))
        self.assertIsNotNone(q["lastUpdatedIso"])
        self.assertTrue(q["disclaimer"].lower().startswith("delayed"))
        # Upstream contract: single call, expected URL and timeout.
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(
            fake.calls[0]["url"],
            yahoo.build_chart_url("0700.HK", "1d"),
        )
        self.assertEqual(fake.calls[0]["timeout"], 3.0)

    def test_accepts_raw_short_code(self):
        fake = FakeUpstream()
        q = yahoo.get_quote("700", fetch_fn=fake)
        self.assertEqual(q["symbol"], "0700.HK")
        self.assertIn("chart/0700.HK", fake.calls[0]["url"])

    def test_missing_previous_close_yields_none_changes(self):
        payload = make_chart_payload()
        payload["chart"]["result"][0]["meta"].pop("chartPreviousClose")
        payload["chart"]["result"][0]["meta"].pop("previousClose")
        fake = FakeUpstream(payload)
        q = yahoo.get_quote("0700.HK", fetch_fn=fake)
        self.assertIsNone(q["change"])
        self.assertIsNone(q["pctChange"])

    def test_upstream_error_payload_raises_symbol_not_found(self):
        fake = FakeUpstream(canned_error_payload("No data found, symbol not found"))
        with self.assertRaises(yahoo.SymbolNotFoundError) as ctx:
            yahoo.get_quote("0700.HK", fetch_fn=fake)
        self.assertIn("No data found", str(ctx.exception))
        self.assertEqual(ctx.exception.status, 404)

    def test_upstream_transport_error(self):
        err = yahoo.YahooHTTPError("Could not reach Yahoo Finance: timed out")
        fake = FakeUpstream(error=err)
        with self.assertRaises(yahoo.YahooHTTPError):
            yahoo.get_quote("0700.HK", fetch_fn=fake)

    def test_invalid_symbol_raises(self):
        with self.assertRaises(SymbolError):
            yahoo.get_quote("not-a-stock", fetch_fn=FakeUpstream())


class TestGetHistory(unittest.TestCase):
    def test_rows_and_url_for_each_range(self):
        expected = {
            "1M": "range=1mo", "3M": "range=3mo", "6M": "range=6mo",
            "1Y": "range=1y", "5Y": "range=5y",
        }
        for key, fragment in expected.items():
            fake = FakeUpstream(make_chart_payload(days=4))
            data = yahoo.get_history("0700", key, fetch_fn=fake)
            self.assertEqual(data["symbol"], "0700.HK", msg=key)
            self.assertEqual(data["range"], key, msg=key)
            self.assertEqual(len(data["rows"]), 4, msg=key)
            self.assertIn(fragment, fake.calls[0]["url"], msg=key)
            self.assertEqual(len(fake.calls), 1, msg=key)

    def test_row_shape_and_date_localization(self):
        # 1768435200 = 2026-01-15 00:00 UTC; +16h -> 2026-01-15 16:00 UTC
        # = 2026-01-16 01:00 HKT: the HKT date rolls past midnight.
        fake = FakeUpstream(make_chart_payload(days=3, last_ts=1768435200 + 16 * 3600))
        data = yahoo.get_history("0700.HK", "6M", fetch_fn=fake)
        rows = data["rows"]
        self.assertEqual(rows[-1]["date"], "2026-01-16")
        for r in rows:
            self.assertRegex(r["date"], r"^\d{4}-\d{2}-\d{2}$")
            for field in ("open", "high", "low", "close"):
                self.assertIsInstance(r[field], float, msg=field)
            self.assertIsInstance(r["volume"], int)

    def test_invalid_ranges(self):
        for bad in ("2M", "1d", "max", "", None, 5):
            with self.assertRaises(yahoo.InvalidRangeError, msg=repr(bad)):
                yahoo.get_history("0700.HK", bad, fetch_fn=FakeUpstream())

    def test_range_lookup_is_case_insensitive(self):
        fake = FakeUpstream(make_chart_payload(days=2))
        data = yahoo.get_history("0700.HK", "1m", fetch_fn=fake)
        self.assertEqual(data["range"], "1M")
        self.assertIn("range=1mo", fake.calls[0]["url"])

    def test_skips_empty_bars(self):
        payload = make_chart_payload(days=3)
        res = payload["chart"]["result"][0]
        res["timestamp"] = res["timestamp"] + [res["timestamp"][-1] + 86400]
        for k in ("open", "high", "low", "close"):
            res["indicators"]["quote"][0][k].append(None)
        res["indicators"]["quote"][0]["volume"].append(None)
        fake = FakeUpstream(payload)
        data = yahoo.get_history("0700.HK", "1M", fetch_fn=fake)
        self.assertEqual(len(data["rows"]), 3)


class TestHkMarketState(unittest.TestCase):
    def _state(self, iso_utc):
        ts = dt.datetime.fromisoformat(iso_utc).replace(tzinfo=dt.timezone.utc)
        return yahoo.hk_market_state(now_utc=ts)

    def test_sessions(self):
        self.assertEqual(self._state("2025-01-06T01:00:00"), "open")    # Mon 10:00 HKT
        self.assertEqual(self._state("2025-01-06T00:00:00"), "closed")  # Mon 09:00 HKT
        self.assertEqual(self._state("2025-01-06T03:30:00"), "closed")  # Mon 12:30 HKT lunch
        self.assertEqual(self._state("2025-01-06T05:00:00"), "open")    # Mon 14:00 HKT
        self.assertEqual(self._state("2025-01-06T07:00:00"), "closed")  # Mon 16:00 HKT close

    def test_weekend(self):
        self.assertEqual(self._state("2025-01-04T01:00:00"), "closed")  # Sat
        self.assertEqual(self._state("2025-01-05T01:00:00"), "closed")  # Sun


class TestFetchJsonGuardrails(unittest.TestCase):
    def test_refuses_disallowed_host(self):
        with self.assertRaises(ValueError):
            yahoo.fetch_json("https://evil.example/v8/finance/chart/0700.HK")

    def test_refuses_lookalike_subdomain(self):
        with self.assertRaises(ValueError):
            yahoo.fetch_json("https://query1.finance.yahoo.com.evil.com/x")

    def test_allows_real_upstream_host(self):
        good = yahoo.build_chart_url("0700.HK", "1d")
        self.assertTrue(any(good.startswith(b + "/") for b in yahoo.ALLOWED_UPSTREAM_BASES))

    def _fake_response(self, body: bytes, status: int = 200):
        class Resp:
            def __init__(self, body):
                self._body = body
            def read(self):
                return self._body
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return Resp(body)

    def test_http_404_maps_to_symbol_not_found(self):
        import urllib.error
        err = urllib.error.HTTPError(
            "https://query1.finance.yahoo.com/v8/finance/chart/0000.HK",
            404, "Not Found", None,
            io.BytesIO(b'{"chart":{"result":null,"error":{"code":"Not Found",'
                       b'"description":"No data found, symbol not found"}}}'))
        with mock.patch.object(yahoo.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(yahoo.SymbolNotFoundError) as ctx:
                yahoo.fetch_json(yahoo.build_chart_url("0000.HK", "1d"))
        self.assertIn("No data found", str(ctx.exception))
        self.assertEqual(ctx.exception.status, 404)

    def test_http_500_maps_to_yahoo_http_error(self):
        # 5xx is retryable: query1's 500 triggers one retry on query2;
        # when that also fails, the PRIMARY host's error is raised.
        import urllib.error
        primary = urllib.error.HTTPError(
            "https://query1.finance.yahoo.com/v8/finance/chart/0700.HK",
            500, "Internal Server Error", None, io.BytesIO(b"oops"))
        fallback = urllib.error.HTTPError(
            "https://query2.finance.yahoo.com/v8/finance/chart/0700.HK",
            502, "Bad Gateway", None, io.BytesIO(b"oops"))
        with mock.patch.object(yahoo.urllib.request, "urlopen",
                               side_effect=[primary, fallback]) as mock_open:
            with self.assertRaises(yahoo.YahooHTTPError) as ctx:
                yahoo.fetch_json(yahoo.build_chart_url("0700.HK", "1d"))
        self.assertEqual(ctx.exception.status, 500)
        # The primary host's error is the one surfaced.
        self.assertEqual(len(mock_open.call_args_list), 2)
        self._assert_fallback_urls(mock_open)

    def test_network_error_maps_to_yahoo_http_error(self):
        # Transport errors are retryable: one retry on query2, then the
        # PRIMARY host's error is surfaced.
        import urllib.error
        with mock.patch.object(yahoo.urllib.request, "urlopen",
                               side_effect=[urllib.error.URLError("timed out"),
                                            urllib.error.URLError("conn refused")]) as mock_open:
            with self.assertRaises(yahoo.YahooHTTPError) as ctx:
                yahoo.fetch_json(yahoo.build_chart_url("0700.HK", "1d"))
        self.assertIn("timed out", str(ctx.exception))
        self.assertEqual(len(mock_open.call_args_list), 2)
        self._assert_fallback_urls(mock_open)

    def test_malformed_json(self):
        with mock.patch.object(yahoo.urllib.request, "urlopen",
                               return_value=self._fake_response(b"this is not json")):
            with self.assertRaises(yahoo.YahooHTTPError):
                yahoo.fetch_json(yahoo.build_chart_url("0700.HK", "1d"))

    def test_valid_json_roundtrip(self):
        payload = make_chart_payload()
        with mock.patch.object(yahoo.urllib.request, "urlopen",
                               return_value=self._fake_response(
                                   json.dumps(payload).encode())):
            data = yahoo.fetch_json(yahoo.build_chart_url("0700.HK", "1d"))
        self.assertEqual(data["chart"]["result"][0]["meta"]["symbol"], "0700.HK")

    def _req_url(self, call):
        arg = call.args[0]
        return getattr(arg, "full_url", None) or arg

    def _assert_fallback_urls(self, mock_open):
        urls = [self._req_url(c) for c in mock_open.call_args_list]
        self.assertEqual(urls[0], yahoo.build_chart_url("0700.HK", "1d"))
        self.assertTrue(urls[0].startswith(
            yahoo.PRIMARY_UPSTREAM_BASE + "/"), urls)
        self.assertEqual(
            urls[1],
            yahoo._fallback_url(yahoo.build_chart_url("0700.HK", "1d")), urls)
        self.assertTrue(urls[1].startswith(
            yahoo.FALLBACK_UPSTREAM_BASE + "/"), urls)
        self.assertNotEqual(urls[0], urls[1])
        # path and query are identical across hosts
        import urllib.parse
        p0 = urllib.parse.urlsplit(urls[0])
        p1 = urllib.parse.urlsplit(urls[1])
        self.assertEqual(p0.path + "?" + p0.query, p1.path + "?" + p1.query)


class TestFetchJsonFallback(unittest.TestCase):
    """query1 -> query2 fallback behavior of fetch_json (mocked urlopen)."""

    def _http_error(self, code, body=b"", host="query1"):
        import urllib.error
        return urllib.error.HTTPError(
            "https://%s.finance.yahoo.com/v8/finance/chart/0700.HK" % host,
            code, "Error", None, io.BytesIO(body))

    def _ok_response(self, payload=None):
        class Resp:
            def __init__(self, body):
                self._body = body
            def read(self):
                return self._body
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        body = json.dumps(payload or make_chart_payload()).encode()
        return Resp(body)

    def _req_url(self, call):
        arg = call.args[0]
        return getattr(arg, "full_url", None) or arg

    def test_primary_success_no_fallback(self):
        url = yahoo.build_chart_url("0700.HK", "1d")
        with mock.patch.object(yahoo.urllib.request, "urlopen",
                               return_value=self._ok_response()) as mock_open:
            data = yahoo.fetch_json(url)
        self.assertEqual(data["chart"]["result"][0]["meta"]["symbol"], "0700.HK")
        self.assertEqual(len(mock_open.call_args_list), 1)
        self.assertEqual(self._req_url(mock_open.call_args_list[0]), url)

    def test_429_triggers_query2_success(self):
        url = yahoo.build_chart_url("0700.HK", "1d")
        with mock.patch.object(yahoo.urllib.request, "urlopen",
                               side_effect=[self._http_error(429,
                                   b'{"chart":{"error":{"description":"Too Many Requests"}}}'),
                                            self._ok_response()]) as mock_open:
            data = yahoo.fetch_json(url)
        self.assertEqual(data["chart"]["result"][0]["meta"]["symbol"], "0700.HK")
        self.assertEqual(len(mock_open.call_args_list), 2)
        urls = [self._req_url(c) for c in mock_open.call_args_list]
        self.assertEqual(urls[1], yahoo._fallback_url(url))
        self.assertTrue(urls[1].startswith(yahoo.FALLBACK_UPSTREAM_BASE + "/"))
        self.assertNotEqual(urls[0], urls[1])

    def test_5xx_triggers_query2_success(self):
        url = yahoo.build_chart_url("0700.HK", "1d")
        with mock.patch.object(yahoo.urllib.request, "urlopen",
                               side_effect=[self._http_error(503),
                                            self._ok_response()]) as mock_open:
            data = yahoo.fetch_json(url)
        self.assertEqual(data["chart"]["result"][0]["meta"]["symbol"], "0700.HK")
        self.assertEqual(len(mock_open.call_args_list), 2)

    def test_transport_error_triggers_query2_success(self):
        import urllib.error
        url = yahoo.build_chart_url("0700.HK", "1d")
        with mock.patch.object(yahoo.urllib.request, "urlopen",
                               side_effect=[urllib.error.URLError("timed out"),
                                            self._ok_response()]) as mock_open:
            data = yahoo.fetch_json(url)
        self.assertEqual(data["chart"]["result"][0]["meta"]["symbol"], "0700.HK")
        self.assertEqual(len(mock_open.call_args_list), 2)

    def test_404_is_not_retried(self):
        url = yahoo.build_chart_url("0000.HK", "1d")
        with mock.patch.object(yahoo.urllib.request, "urlopen",
                               side_effect=[self._http_error(
                                   404, b'{"chart":{"error":{"code":"Not Found",'
                                        b'"description":"No data found, symbol not found"}}}')]) as mock_open:
            with self.assertRaises(yahoo.SymbolNotFoundError) as ctx:
                yahoo.fetch_json(url)
        self.assertIn("No data found", str(ctx.exception))
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(len(mock_open.call_args_list), 1)  # never fell back

    def test_non_retryable_4xx_is_not_retried(self):
        url = yahoo.build_chart_url("0700.HK", "1d")
        with mock.patch.object(yahoo.urllib.request, "urlopen",
                               side_effect=[self._http_error(403)]) as mock_open:
            with self.assertRaises(yahoo.YahooHTTPError) as ctx:
                yahoo.fetch_json(url)
        self.assertEqual(ctx.exception.status, 403)
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(len(mock_open.call_args_list), 1)

    def test_both_hosts_fail_raises_primary_error(self):
        import urllib.error
        url = yahoo.build_chart_url("0700.HK", "1d")
        with mock.patch.object(yahoo.urllib.request, "urlopen",
                               side_effect=[self._http_error(429,
                                   b'{"chart":{"error":{"description":"Too Many Requests"}}}'),
                                            urllib.error.URLError("conn refused")]) as mock_open:
            with self.assertRaises(yahoo.YahooHTTPError) as ctx:
                yahoo.fetch_json(url)
        # The primary host's error is the one surfaced.
        self.assertIn("Too Many Requests", str(ctx.exception))
        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(len(mock_open.call_args_list), 2)  # exactly one retry

    def test_no_third_attempt_after_both_failures(self):
        import urllib.error
        url = yahoo.build_chart_url("0700.HK", "1d")
        # A third retry would exhaust side_effect and raise StopIteration.
        with mock.patch.object(yahoo.urllib.request, "urlopen",
                               side_effect=[urllib.error.URLError("down"),
                                            self._http_error(500, host="query2")]) as mock_open:
            with self.assertRaises(yahoo.YahooHTTPError) as ctx:
                yahoo.fetch_json(url)
        # Primary's transport error is surfaced (transport errors have no status).
        self.assertIsNone(ctx.exception.status)
        self.assertIn("Could not reach Yahoo Finance", str(ctx.exception))
        self.assertEqual(len(mock_open.call_args_list), 2)

    def test_fallback_response_404_keeps_not_found_semantics(self):
        import urllib.error
        url = yahoo.build_chart_url("0700.HK", "1d")
        with mock.patch.object(yahoo.urllib.request, "urlopen",
                               side_effect=[urllib.error.URLError("down"),
                                            self._http_error(
                                                404,
                                                b'{"chart":{"error":{"code":"Not Found",'
                                                b'"description":"No data found, symbol not found"}}}',
                                                host="query2")]) as mock_open:
            with self.assertRaises(yahoo.SymbolNotFoundError):
                yahoo.fetch_json(url)
        self.assertEqual(len(mock_open.call_args_list), 2)

    def test_malformed_primary_response_is_not_retried(self):
        class BadResp:
            def read(self):
                return b"this is not json"
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        url = yahoo.build_chart_url("0700.HK", "1d")
        with mock.patch.object(yahoo.urllib.request, "urlopen",
                               side_effect=[BadResp(), self._ok_response()]) as mock_open:
            with self.assertRaises(yahoo.YahooHTTPError) as ctx:
                yahoo.fetch_json(url)
        self.assertIn("malformed", str(ctx.exception))
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(len(mock_open.call_args_list), 1)  # no masking via retry

    def test_fallback_host_url_is_allowed_but_never_retried(self):
        # A URL already on query2 is allowed by the allowlist...
        q2 = yahoo._fallback_url(yahoo.build_chart_url("0700.HK", "1d"))
        self.assertIsNotNone(q2)
        with mock.patch.object(yahoo.urllib.request, "urlopen",
                               return_value=self._ok_response()) as mock_open:
            data = yahoo.fetch_json(q2)
        self.assertEqual(data["chart"]["result"][0]["meta"]["symbol"], "0700.HK")
        self.assertEqual(len(mock_open.call_args_list), 1)
        # ...and a retryable failure on query2 is NOT retried a third time.
        with mock.patch.object(yahoo.urllib.request, "urlopen",
                               side_effect=[self._http_error(429, host="query2")]) as mock_open:
            with self.assertRaises(yahoo.YahooHTTPError) as ctx:
                yahoo.fetch_json(q2)
        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(len(mock_open.call_args_list), 1)

    def test_lookalike_hosts_rejected(self):
        for bad in (
            "https://query1.finance.yahoo.com.evil.com/v8/finance/chart/0700.HK",
            "https://query2.finance.yahoo.com.evil.com/v8/finance/chart/0700.HK",
            "https://evil.com/query1.finance.yahoo.com/v8/finance/chart/0700.HK",
            "http://query1.finance.yahoo.com/v8/finance/chart/0700.HK",
            "https://query1.finance.yahoo.com.evil.com@evil.com/v8/finance/chart/0700.HK",
        ):
            with self.assertRaises(ValueError, msg=bad):
                yahoo.fetch_json(bad)

    def test_fallback_url_helper(self):
        url = yahoo.build_chart_url("0700.HK", "1d")
        fb = yahoo._fallback_url(url)
        self.assertEqual(
            fb,
            "https://query2.finance.yahoo.com/v8/finance/chart/0700.HK"
            "?range=1d&interval=1d&events=div%2Csplit",
        )
        self.assertIsNone(yahoo._fallback_url(fb))  # no fallback of fallback
        self.assertIsNone(yahoo._fallback_url("https://evil.example/x"))


if __name__ == "__main__":
    unittest.main()
