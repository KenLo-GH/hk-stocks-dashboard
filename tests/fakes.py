"""Canned Yahoo Finance payloads and a fake fetch for tests."""

from __future__ import annotations

import json


def make_chart_payload(price=400.6, prev_close=396.0, name="Tencent Holdings Ltd.",
                       symbol="0700.HK", days=3, last_ts=1768435200,
                       day_high=403.2, day_low=395.1, volume=123456789):
    """Build a minimal but realistic v8 chart payload (daily bars, HKT offset)."""
    bars = []
    opens, highs, lows, closes, vols = [], [], [], [], []
    base_ts = last_ts - (days - 1) * 86400
    for i in range(days):
        o = prev_close + i
        c = price if i == days - 1 else prev_close + i + 0.5
        h = max(o, c) + 1.0
        l = min(o, c) - 1.0
        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)
        vols.append(volume - i * 1000)
        bars.append({"t": base_ts + i * 86400, "o": o, "h": h, "l": l, "c": c, "v": vols[-1]})
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "HKD",
                        "symbol": symbol,
                        "longName": name,
                        "shortName": name.split()[0],
                        "fullExchangeName": "HKEX",
                        "exchange": "HKG",
                        "regularMarketPrice": price,
                        "chartPreviousClose": prev_close,
                        "previousClose": prev_close,
                        "regularMarketDayHigh": day_high,
                        "regularMarketDayLow": day_low,
                        "regularMarketVolume": volume,
                        "regularMarketTime": last_ts,
                        "gmtoffset": 9 * 3600,
                        "exchangeTimezoneName": "Asia/Hong_Kong",
                    },
                    "timestamp": [b["t"] for b in bars],
                    "indicators": {
                        "quote": [
                            {"open": opens, "high": highs, "low": lows,
                             "close": closes, "volume": vols}
                        ],
                        "adjclose": [{"adjclose": closes}],
                    },
                }
            ],
            "error": None,
        }
    }


class FakeUpstream:
    """Stand-in for the Yahoo chart endpoint.

    Records every URL it serves so tests can assert on what the app
    requested upstream.
    """

    def __init__(self, payload=None, error=None):
        self.payload = payload or make_chart_payload()
        self.error = error  # exception instance to raise instead of serving
        self.calls = []

    def __call__(self, url, timeout=10.0):
        self.calls.append({"url": url, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return json.loads(json.dumps(self.payload))  # deep copy


def canned_error_payload(description, code="Invalid Input", status=200):
    """Payload shape Yahoo returns when the request itself is fine but the
    symbol is unknown (HTTP 200 + embedded chart.error)."""
    return {
        "chart": {
            "result": None,
            "error": {"code": code, "description": description},
        }
    }


import datetime as _dt


def make_em_quote_payload(code="00700", name="Tencent Holdings",
                          f43=436800, f44=439800, f45=435000, f46=435000,
                          f47=4095012, f60=441000, f170=-95,
                          f86=1790216967, f59=3):
    """Build a realistic EastMoney push2 stock/get payload.

    Prices are scaled by 10**f59 (like the live API: f43=436800 with
    f59=3 -> 436.8). f170 is the percent change in basis points.
    """
    return {
        "rc": 0,
        "rt": 4,
        "svr": 177622154,
        "data": {
            "f43": f43, "f44": f44, "f45": f45, "f46": f46,
            "f47": f47, "f48": 1790759072.0,
            "f57": code, "f58": name, "f59": f59,
            "f60": f60, "f86": f86, "f170": f170,
        },
    }


def make_em_kline_payload(rows=5, end_date="2026-09-24", code="00700"):
    """Build a realistic EastMoney push2his kline payload.

    Rows follow the live format: date,open,close,high,low,volume,amount,...
    (close is column 2, NOT column 5).
    """
    lines = []
    for i in range(rows):
        d = (_dt.date.fromisoformat(end_date)
             - _dt.timedelta(days=rows - 1 - i)).isoformat()
        o, c = 470.0 + i, 475.0 + i
        h, l, v = o + 10.0, o - 5.0, 12_000_000 + i * 1000
        lines.append(
            "%s,%.3f,%.3f,%.3f,%.3f,%d,%.1f,1.0,0.0,%.3f,0.4"
            % (d, o, c, h, l, v, v * c, 0.5)
        )
    return {
        "rc": 0,
        "rt": 17,
        "data": {"code": code, "market": 116, "name": "Tencent Holdings",
                 "decimal": 3, "klines": lines},
    }


def em_error_payload(rc=1102):
    """EastMoney payload for an unknown secid (no ``data`` block)."""
    return {"rc": rc, "rt": 4, "data": None}


class MultiCallUpstream:
    """Serves a sequence of canned behaviors (payload or exception) per call.

    Like :class:`FakeUpstream` but each call can return a different
    behavior; after the last behavior it keeps repeating it. Records every
    URL it is called with.
    """

    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.calls = []

    def __call__(self, url, timeout=10.0):
        self.calls.append({"url": url, "timeout": timeout})
        behavior = (self.behaviors.pop(0) if len(self.behaviors) > 1
                    else self.behaviors[0])
        if isinstance(behavior, Exception):
            raise behavior
        return json.loads(json.dumps(behavior))  # deep copy
