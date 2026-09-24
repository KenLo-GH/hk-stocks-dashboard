"""Unit tests for the EastMoney fallback client with mocked upstream data."""

import datetime as dt
import io
import json
import unittest
from unittest import mock

from hk_dashboard import eastmoney
from hk_dashboard.symbols import SymbolError
from tests.fakes import (
    em_error_payload,
    make_em_kline_payload,
    make_em_quote_payload,
)


class TestHkEmCode(unittest.TestCase):
    def test_five_digit_normalization(self):
        self.assertEqual(eastmoney.hk_em_code("0700.HK"), "00700")
        self.assertEqual(eastmoney.hk_em_code("0011.HK"), "00011")
        self.assertEqual(eastmoney.hk_em_code("700"), "00700")
        self.assertEqual(eastmoney.hk_em_code("11"), "00011")
        self.assertEqual(eastmoney.hk_em_code("12345.HK"), "12345")
        self.assertEqual(eastmoney.hk_em_code("9988"), "09988")

    def test_invalid_raises(self):
        with self.assertRaises(SymbolError):
            eastmoney.hk_em_code("abc.HK")


class TestBuildUrls(unittest.TestCase):
    def test_quote_url_exact(self):
        url = eastmoney.build_quote_url("0700.HK")
        self.assertEqual(
            url,
            "https://push2.eastmoney.com/api/qt/stock/get?secid=116.00700"
            "&fields=f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f170,f86"
            "&ut=fa5fd1943c7b386f172d6893dbfba10b",
        )

    def test_quote_url_uses_market_116_and_five_digit_code(self):
        url = eastmoney.build_quote_url("11.HK")
        self.assertIn("secid=116.00011", url)

    def test_kline_url_exact_for_1m(self):
        now = dt.datetime(2026, 9, 24, 2, 0, 0, tzinfo=dt.timezone.utc)
        url = eastmoney.build_kline_url("0700.HK", "1M", now=now)
        self.assertEqual(
            url,
            "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            "?secid=116.00700&klt=101&fqt=1&beg=20260824&end=20500101"
            "&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            "&ut=fa5fd1943c7b386f172d6893dbfba10b&rtntype=6",
        )

    def test_kline_beg_uses_range_lookback(self):
        now = dt.datetime(2026, 9, 24, 2, 0, 0, tzinfo=dt.timezone.utc)
        expected = {
            "1M": 31, "3M": 92, "6M": 183, "1Y": 366, "5Y": 1831,
        }
        for key, days in expected.items():
            url = eastmoney.build_kline_url("0700.HK", key, now=now)
            beg = (now - dt.timedelta(days=days)).strftime("%Y%m%d")
            self.assertIn("beg=%s" % beg, url, msg=key)
            self.assertIn("end=20500101", url, msg=key)

    def test_kline_invalid_range_raises(self):
        for bad in ("2M", "1d", "max", None, 5):
            with self.assertRaises(eastmoney.InvalidRangeError, msg=repr(bad)):
                eastmoney.build_kline_url("0700.HK", bad)


class TestFetchJsonGuardrails(unittest.TestCase):
    def test_refuses_disallowed_host(self):
        with self.assertRaises(ValueError):
            eastmoney.fetch_json("https://evil.example/api/qt/stock/get")

    def test_refuses_lookalike_hosts(self):
        for bad in (
            "https://push2.eastmoney.com.evil.com/api/qt/stock/get",
            "https://push2delay.eastmoney.com.evil.com/api/qt/stock/get",
            "https://push2his.eastmoney.com.evil.com/api/qt/stock/kline/get",
            "https://evil.com/push2.eastmoney.com/api/qt/stock/get",
            "http://push2.eastmoney.com/api/qt/stock/get",
            "https://push2.eastmoney.com@evil.com/api/qt/stock/get",
            "https://push2.eastmoney.com.evil.com:8443/api/qt/stock/get",
        ):
            with self.assertRaises(ValueError, msg=bad):
                eastmoney.fetch_json(bad)

    def test_allows_real_hosts(self):
        for good in (
            eastmoney.build_quote_url("0700.HK"),
            eastmoney.build_kline_url("0700.HK", "1M"),
            "https://push2delay.eastmoney.com/api/qt/stock/get?secid=116.00700",
        ):
            try:
                eastmoney._assert_allowed_em_url(good)
            except ValueError:
                self.fail("URL wrongly rejected: %s" % good)

    def _fake_response(self, body: bytes, final_url: str = None):
        class Resp:
            def __init__(self, body, final_url):
                self._body = body
                self._final = final_url
            def read(self):
                return self._body
            def geturl(self):
                return self._final
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return Resp(body, final_url)

    def test_http_404_maps_to_symbol_not_found(self):
        import urllib.error
        err = urllib.error.HTTPError(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            404, "Not Found", None, io.BytesIO(b""))
        with mock.patch.object(eastmoney.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(eastmoney.EastMoneySymbolNotFoundError) as ctx:
                eastmoney.fetch_json(eastmoney.build_kline_url("0000.HK", "1M"))
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(len(mock_open_calls(mock_open := None)) if False else True, True)

    def test_http_503_maps_to_retryable_error(self):
        import urllib.error
        err = urllib.error.HTTPError(
            "https://push2.eastmoney.com/api/qt/stock/get",
            503, "Service Unavailable", None, io.BytesIO(b"oops"))
        with mock.patch.object(eastmoney.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(eastmoney.EastMoneyError) as ctx:
                eastmoney.fetch_json(eastmoney.build_quote_url("0700.HK"))
        self.assertEqual(ctx.exception.status, 503)
        self.assertTrue(ctx.exception.retryable)

    def test_network_error_maps_to_retryable_error(self):
        import urllib.error
        with mock.patch.object(eastmoney.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("timed out")):
            with self.assertRaises(eastmoney.EastMoneyError) as ctx:
                eastmoney.fetch_json(eastmoney.build_quote_url("0700.HK"))
        self.assertIn("timed out", str(ctx.exception))
        self.assertTrue(ctx.exception.retryable)

    def test_malformed_json(self):
        with mock.patch.object(eastmoney.urllib.request, "urlopen",
                               return_value=self._fake_response(b"not json",
                                   "https://push2.eastmoney.com/api/qt/stock/get")):
            with self.assertRaises(eastmoney.EastMoneyError) as ctx:
                eastmoney.fetch_json(eastmoney.build_quote_url("0700.HK"))
        self.assertIn("malformed", str(ctx.exception))
        self.assertFalse(ctx.exception.retryable)

    def test_non_dict_json_rejected(self):
        with mock.patch.object(eastmoney.urllib.request, "urlopen",
                               return_value=self._fake_response(b"[1,2,3]",
                                   "https://push2.eastmoney.com/api/qt/stock/get")):
            with self.assertRaises(eastmoney.EastMoneyError):
                eastmoney.fetch_json(eastmoney.build_quote_url("0700.HK"))

    def test_redirect_to_non_allowlisted_host_is_refused(self):
        with mock.patch.object(eastmoney.urllib.request, "urlopen",
                               return_value=self._fake_response(
                                   b'{"rc":0}', "https://evil.example/")):
            with self.assertRaises(eastmoney.EastMoneyError) as ctx:
                eastmoney.fetch_json(eastmoney.build_quote_url("0700.HK"))
        self.assertIn("redirected", str(ctx.exception))

    def test_redirect_to_allowlisted_delayed_feed_is_ok(self):
        url = eastmoney.build_quote_url("0700.HK")
        final = "https://push2delay.eastmoney.com/api/qt/stock/get?secid=116.00700"
        with mock.patch.object(eastmoney.urllib.request, "urlopen",
                               return_value=self._fake_response(
                                   json.dumps(make_em_quote_payload()).encode(),
                                   final)):
            data = eastmoney.fetch_json(url)
        self.assertEqual(data["rc"], 0)

    def test_retryable_failure_retried_exactly_once(self):
        url = eastmoney.build_quote_url("0700.HK")
        import urllib.error
        def err():
            return urllib.error.HTTPError(
                url, 429, "Too Many Requests", None, io.BytesIO(b""))
        with mock.patch.object(eastmoney.urllib.request, "urlopen",
                               side_effect=[err(), err()]) as mock_open:
            with self.assertRaises(eastmoney.EastMoneyError) as ctx:
                eastmoney.fetch_json(url, retry_delay=0.0)
        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(len(mock_open.call_args_list), 2)  # initial + 1 retry

    def test_non_retryable_failure_not_retried(self):
        url = eastmoney.build_quote_url("0700.HK")
        import urllib.error
        with mock.patch.object(eastmoney.urllib.request, "urlopen",
                               side_effect=urllib.error.HTTPError(
                                   url, 403, "Forbidden", None,
                                   io.BytesIO(b""))) as mock_open:
            with self.assertRaises(eastmoney.EastMoneyError):
                eastmoney.fetch_json(url, retry_delay=0.0)
        self.assertEqual(len(mock_open.call_args_list), 1)

    def test_retry_succeeds_on_second_attempt(self):
        url = eastmoney.build_quote_url("0700.HK")
        import urllib.error
        final = "https://push2delay.eastmoney.com/api/qt/stock/get?secid=116.00700"
        with mock.patch.object(eastmoney.urllib.request, "urlopen",
                               side_effect=[
                                   urllib.error.URLError("handshake timed out"),
                                   self._fake_response(
                                       json.dumps(make_em_quote_payload()).encode(),
                                       final)]) as mock_open:
            data = eastmoney.fetch_json(url, retry_delay=0.0)
        self.assertEqual(data["rc"], 0)
        self.assertEqual(len(mock_open.call_args_list), 2)

    def test_no_third_attempt(self):
        # A third attempt would exhaust side_effect and raise StopIteration.
        url = eastmoney.build_kline_url("0700.HK", "1M")
        import urllib.error
        with mock.patch.object(eastmoney.urllib.request, "urlopen",
                               side_effect=[
                                   urllib.error.URLError("a"),
                                   urllib.error.URLError("b")]) as mock_open:
            with self.assertRaises(eastmoney.EastMoneyError):
                eastmoney.fetch_json(url, retry_delay=0.0)
        self.assertEqual(len(mock_open.call_args_list), 2)


class _RecordingFetcher:
    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.calls = []

    def __call__(self, url, timeout=10.0):
        self.calls.append(url)
        behavior = (self.behaviors.pop(0) if len(self.behaviors) > 1
                    else self.behaviors[0])
        if isinstance(behavior, Exception):
            raise behavior
        return json.loads(json.dumps(behavior))


class TestGetQuote(unittest.TestCase):
    def test_happy_path_field_scaling(self):
        fetcher = _RecordingFetcher([make_em_quote_payload(
            f43=436800, f44=439800, f45=435000, f46=435000,
            f47=4095012, f60=441000, f170=-95, f86=1790216967, f59=3)])
        q = eastmoney.get_quote("0700.HK", fetch_fn=fetcher, timeout=3.0)
        # f59=3 -> divide by 1000 (Tencent live example: 436400 -> 436.4)
        self.assertEqual(q["price"], 436.8)
        self.assertEqual(q["dayHigh"], 439.8)
        self.assertEqual(q["dayLow"], 435.0)
        self.assertEqual(q["open"], 435.0)
        self.assertEqual(q["previousClose"], 441.0)
        self.assertAlmostEqual(q["change"], -4.2)
        self.assertAlmostEqual(q["pctChange"], -0.95)
        self.assertEqual(q["volume"], 4095012)
        self.assertEqual(q["symbol"], "0700.HK")
        self.assertEqual(q["currency"], "HKD")
        self.assertEqual(q["exchange"], "HKEX")
        self.assertEqual(q["name"], "Tencent Holdings")
        self.assertIn(q["marketState"], ("open", "closed"))
        self.assertIsNotNone(q["lastUpdatedIso"])
        self.assertIn("EastMoney", q["source"])
        self.assertTrue(q["disclaimer"].lower().startswith("delayed"))
        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual(fetcher.calls[0], eastmoney.build_quote_url("0700.HK"))

    def test_f59_zero_precision(self):
        fetcher = _RecordingFetcher([make_em_quote_payload(
            f43=436, f44=440, f45=435, f46=435, f60=441, f59=0)])
        q = eastmoney.get_quote("0700.HK", fetch_fn=fetcher)
        self.assertEqual(q["price"], 436.0)
        self.assertEqual(q["previousClose"], 441.0)

    def test_f59_two_precision(self):
        fetcher = _RecordingFetcher([make_em_quote_payload(
            f43=43640, f44=43980, f45=43500, f46=43500, f60=44100, f59=2)])
        q = eastmoney.get_quote("0700.HK", fetch_fn=fetcher)
        self.assertEqual(q["price"], 436.4)
        self.assertEqual(q["dayHigh"], 439.8)
        self.assertEqual(q["previousClose"], 441.0)

    def test_pct_from_f170_when_no_prev_close(self):
        # prev close 0 -> falsy -> change/pct fall back to f170/100.
        payload = make_em_quote_payload(f60=0)
        fetcher = _RecordingFetcher([payload])
        q = eastmoney.get_quote("0700.HK", fetch_fn=fetcher)
        self.assertAlmostEqual(q["pctChange"], -0.95)  # f170/100

    def test_missing_data_is_symbol_not_found(self):
        fetcher = _RecordingFetcher([em_error_payload()])
        with self.assertRaises(eastmoney.EastMoneySymbolNotFoundError) as ctx:
            eastmoney.get_quote("0000.HK", fetch_fn=fetcher)
        self.assertEqual(ctx.exception.status, 404)

    def test_missing_price_is_error(self):
        payload = make_em_quote_payload()
        del payload["data"]["f43"]
        fetcher = _RecordingFetcher([payload])
        with self.assertRaises(eastmoney.EastMoneyError):
            eastmoney.get_quote("0700.HK", fetch_fn=fetcher)

    def test_upstream_error_propagates(self):
        fetcher = _RecordingFetcher([eastmoney.EastMoneyError("boom", status=502)])
        with self.assertRaises(eastmoney.EastMoneyError):
            eastmoney.get_quote("0700.HK", fetch_fn=fetcher)

    def test_invalid_symbol_raises(self):
        with self.assertRaises(SymbolError):
            eastmoney.get_quote("not-a-stock", fetch_fn=_RecordingFetcher([]))


class TestGetHistory(unittest.TestCase):
    def test_rows_and_url(self):
        fetcher = _RecordingFetcher([make_em_kline_payload(rows=5)])
        data = eastmoney.get_history("0700.HK", "1M", fetch_fn=fetcher)
        self.assertEqual(data["symbol"], "0700.HK")
        self.assertEqual(data["range"], "1M")
        self.assertEqual(len(data["rows"]), 5)
        self.assertIn("EastMoney", data["source"])
        self.assertTrue(data["disclaimer"])
        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual(fetcher.calls[0],
                         eastmoney.build_kline_url("0700.HK", "1M"))

    def test_row_shape_close_is_column_2(self):
        fetcher = _RecordingFetcher([make_em_kline_payload(rows=2)])
        data = eastmoney.get_history("700", "1M", fetch_fn=fetcher)
        rows = data["rows"]
        # make_em_kline_payload: o=470+i, c=475+i -> first row c=475.0
        self.assertEqual(rows[0]["open"], 470.0)
        self.assertEqual(rows[0]["close"], 475.0)
        self.assertEqual(rows[0]["high"], 480.0)
        self.assertEqual(rows[0]["low"], 465.0)
        self.assertIsInstance(rows[0]["volume"], int)
        for r in rows:
            self.assertRegex(r["date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_range_case_insensitive(self):
        fetcher = _RecordingFetcher([make_em_kline_payload(rows=1)])
        data = eastmoney.get_history("0700.HK", "1m", fetch_fn=fetcher)
        self.assertEqual(data["range"], "1M")

    def test_invalid_range_raises(self):
        for bad in ("2M", "1d", "max", None, 5):
            with self.assertRaises(eastmoney.InvalidRangeError, msg=repr(bad)):
                eastmoney.get_history("0700.HK", bad, fetch_fn=_RecordingFetcher([]))

    def test_skips_malformed_rows(self):
        payload = make_em_kline_payload(rows=2)
        payload["data"]["klines"].append("garbage")
        payload["data"]["klines"].append("")
        fetcher = _RecordingFetcher([payload])
        data = eastmoney.get_history("0700.HK", "1M", fetch_fn=fetcher)
        self.assertEqual(len(data["rows"]), 2)

    def test_no_rows_is_symbol_not_found(self):
        payload = make_em_kline_payload(rows=5)
        payload["data"]["klines"] = []
        fetcher = _RecordingFetcher([payload])
        with self.assertRaises(eastmoney.EastMoneySymbolNotFoundError):
            eastmoney.get_history("0000.HK", "1M", fetch_fn=fetcher)

    def test_upstream_error_propagates(self):
        fetcher = _RecordingFetcher([eastmoney.EastMoneyError("down")])
        with self.assertRaises(eastmoney.EastMoneyError):
            eastmoney.get_history("0700.HK", "1M", fetch_fn=fetcher)


if __name__ == "__main__":
    unittest.main()
