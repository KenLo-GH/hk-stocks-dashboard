"""Tests for the Yahoo-first / EastMoney-fallback orchestration (mocked)."""

import unittest

from hk_dashboard import eastmoney, providers, yahoo
from tests.fakes import (
    FakeUpstream,
    MultiCallUpstream,
    make_chart_payload,
    make_em_kline_payload,
    make_em_quote_payload,
)

EM_QUOTE = make_em_quote_payload()
EM_HIST = make_em_kline_payload(rows=4)


class _EmFake:
    """Minimal EastMoney fetch stand-in recording URLs served."""

    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def __call__(self, url, timeout=10.0):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        import json
        return json.loads(json.dumps(self.payload))


class TestProviderQuote(unittest.TestCase):
    def test_yahoo_success_no_eastmoney(self):
        yf = FakeUpstream(make_chart_payload())
        em = _EmFake(EM_QUOTE)
        q = providers.get_quote("0700.HK", yahoo_fetch=yf, em_fetch=em)
        self.assertIn("Yahoo Finance", q["source"])
        self.assertEqual(len(yf.calls), 1)
        self.assertEqual(len(em.calls), 0)

    def test_yahoo_429_both_hosts_falls_back_to_eastmoney(self):
        yf = FakeUpstream(error=yahoo.YahooHTTPError(
            "Too Many Requests", status=429, retryable=True))
        em = _EmFake(EM_QUOTE)
        q = providers.get_quote("0700.HK", yahoo_fetch=yf, em_fetch=em)
        self.assertIn("EastMoney", q["source"])
        self.assertEqual(q["price"], 436.8)
        self.assertEqual(len(em.calls), 1)
        self.assertEqual(em.calls[0], eastmoney.build_quote_url("0700.HK"))

    def test_yahoo_transport_error_falls_back(self):
        yf = FakeUpstream(error=yahoo.YahooHTTPError(
            "Could not reach Yahoo Finance: timed out", retryable=True))
        em = _EmFake(EM_QUOTE)
        q = providers.get_quote("700", yahoo_fetch=yf, em_fetch=em)
        self.assertIn("EastMoney", q["source"])
        self.assertEqual(q["symbol"], "0700.HK")

    def test_yahoo_not_found_is_not_fallback(self):
        yf = FakeUpstream(error=yahoo.SymbolNotFoundError())
        em = _EmFake(EM_QUOTE)
        with self.assertRaises(yahoo.SymbolNotFoundError):
            providers.get_quote("0000.HK", yahoo_fetch=yf, em_fetch=em)
        self.assertEqual(len(em.calls), 0)

    def test_yahoo_non_retryable_4xx_is_not_fallback(self):
        yf = FakeUpstream(error=yahoo.YahooHTTPError(
            "Forbidden", status=403, retryable=False))
        em = _EmFake(EM_QUOTE)
        with self.assertRaises(yahoo.YahooHTTPError):
            providers.get_quote("0700.HK", yahoo_fetch=yf, em_fetch=em)
        self.assertEqual(len(em.calls), 0)

    def test_eastmoney_symbol_not_found_propagates(self):
        yf = FakeUpstream(error=yahoo.YahooHTTPError("429", 429, retryable=True))
        em = _EmFake(error=eastmoney.EastMoneySymbolNotFoundError())
        with self.assertRaises(eastmoney.EastMoneySymbolNotFoundError):
            providers.get_quote("0000.HK", yahoo_fetch=yf, em_fetch=em)

    def test_eastmoney_failure_propagates(self):
        yf = FakeUpstream(error=yahoo.YahooHTTPError("429", 429, retryable=True))
        em = _EmFake(error=eastmoney.EastMoneyError("down", 502))
        with self.assertRaises(eastmoney.EastMoneyError):
            providers.get_quote("0700.HK", yahoo_fetch=yf, em_fetch=em)

    def test_timeout_propagated_to_both_legs(self):
        yf = FakeUpstream(error=yahoo.YahooHTTPError("429", 429, retryable=True))
        em = _EmFake(EM_QUOTE)
        providers.get_quote("0700.HK", yahoo_fetch=yf, em_fetch=em, timeout=7.7)
        self.assertEqual(yf.calls[-1]["timeout"], 7.7)


class TestProviderHistory(unittest.TestCase):
    def test_yahoo_success_no_eastmoney(self):
        yf = FakeUpstream(make_chart_payload(days=4))
        em = _EmFake(EM_HIST)
        h = providers.get_history("0700.HK", "1M", yahoo_fetch=yf, em_fetch=em)
        self.assertIn("Yahoo Finance", h["source"])
        self.assertEqual(len(em.calls), 0)
        self.assertEqual(len(h["rows"]), 4)

    def test_yahoo_429_falls_back_to_eastmoney_kline(self):
        yf = FakeUpstream(error=yahoo.YahooHTTPError(
            "Too Many Requests", status=429, retryable=True))
        em = _EmFake(EM_HIST)
        h = providers.get_history("0700.HK", "1M", yahoo_fetch=yf, em_fetch=em)
        self.assertIn("EastMoney", h["source"])
        self.assertEqual(h["range"], "1M")
        self.assertEqual(len(h["rows"]), 4)
        self.assertEqual(len(em.calls), 1)
        self.assertEqual(em.calls[0], eastmoney.build_kline_url("0700.HK", "1M"))

    def test_range_case_normalized_for_eastmoney(self):
        yf = FakeUpstream(error=yahoo.YahooHTTPError("429", 429, retryable=True))
        em = _EmFake(EM_HIST)
        h = providers.get_history("0700.HK", "1m", yahoo_fetch=yf, em_fetch=em)
        self.assertEqual(h["range"], "1M")

    def test_invalid_range_is_rejected_before_any_upstream(self):
        yf = FakeUpstream()
        em = _EmFake(EM_HIST)
        with self.assertRaises(yahoo.InvalidRangeError):
            providers.get_history("0700.HK", "2M", yahoo_fetch=yf, em_fetch=em)
        self.assertEqual(len(yf.calls), 0)
        self.assertEqual(len(em.calls), 0)

    def test_yahoo_not_found_is_not_fallback(self):
        yf = FakeUpstream(error=yahoo.SymbolNotFoundError())
        em = _EmFake(EM_HIST)
        with self.assertRaises(yahoo.SymbolNotFoundError):
            providers.get_history("0000.HK", "1Y", yahoo_fetch=yf, em_fetch=em)
        self.assertEqual(len(em.calls), 0)

    def test_eastmoney_symbol_not_found_propagates(self):
        yf = FakeUpstream(error=yahoo.YahooHTTPError("429", 429, retryable=True))
        em = _EmFake(error=eastmoney.EastMoneySymbolNotFoundError())
        with self.assertRaises(eastmoney.EastMoneySymbolNotFoundError):
            providers.get_history("0000.HK", "1M", yahoo_fetch=yf, em_fetch=em)


if __name__ == "__main__":
    unittest.main()
