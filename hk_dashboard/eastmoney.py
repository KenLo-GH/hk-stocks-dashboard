"""Read-only EastMoney fallback client (final fallback after Yahoo fails).

EastMoney is contacted **only** when the Yahoo Finance client is
unavailable or rate-limited (HTTP 429 / 5xx / transport failure after the
query1 -> query2 retry). It is read-only: the app never writes anything.

Only the exact HTTPS hosts in :data:`ALLOWED_EM_HOSTS` are ever contacted:

* ``push2.eastmoney.com``     – quote endpoint; the server may 302-redirect
  to the delayed feed (see next item)
* ``push2delay.eastmoney.com`` – delayed quote feed (the redirect target)
* ``push2his.eastmoney.com``   – daily kline (candlestick) history

URLs are built from hardcoded paths plus a server-validated/normalized
symbol and a whitelisted range key, so user input can never steer requests
to an arbitrary upstream host. Both the request URL *and* any redirect
target are checked against the allowlist, so lookalike hosts (e.g.
``https://push2.eastmoney.com.evil.com``) and non-HTTPS schemes are
rejected.
"""

from __future__ import annotations

import datetime as _dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import symbols
from .yahoo import DEFAULT_TIMEOUT, InvalidRangeError, RANGES, USER_AGENT, hk_market_state

__all__ = [
    "QUOTE_HOST",
    "QUOTE_DELAY_HOST",
    "HISTORY_HOST",
    "QUOTE_BASE",
    "QUOTE_DELAY_BASE",
    "HISTORY_BASE",
    "ALLOWED_EM_HOSTS",
    "ALLOWED_EM_BASES",
    "HK_MARKET_ID",
    "UT_TOKEN",
    "EM_RANGE_DAYS",
    "EastMoneyError",
    "EastMoneySymbolNotFoundError",
    "hk_em_code",
    "build_quote_url",
    "build_kline_url",
    "fetch_json",
    "get_quote",
    "get_history",
]

QUOTE_HOST = "push2.eastmoney.com"
QUOTE_DELAY_HOST = "push2delay.eastmoney.com"
HISTORY_HOST = "push2his.eastmoney.com"

QUOTE_BASE = "https://" + QUOTE_HOST
QUOTE_DELAY_BASE = "https://" + QUOTE_DELAY_HOST
HISTORY_BASE = "https://" + HISTORY_HOST

#: The only EastMoney hosts this app will ever talk to (quote, delayed-quote
#: redirect target, and daily kline history).
ALLOWED_EM_HOSTS = (QUOTE_HOST, QUOTE_DELAY_HOST, HISTORY_HOST)
ALLOWED_EM_BASES = (QUOTE_BASE, QUOTE_DELAY_BASE, HISTORY_BASE)

#: EastMoney market id for the Hong Kong Stock Exchange.
HK_MARKET_ID = "116"

#: Hardcoded EastMoney API token (public, used by their own web app).
UT_TOKEN = "fa5fd1943c7b386f172d6893dbfba10b"

QUOTE_PATH = "/api/qt/stock/get"
KLINE_PATH = "/api/qt/stock/kline/get"
QUOTE_FIELDS = "f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f170,f86"
KLINE_FIELDS1 = "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
KLINE_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
KLINE_END = "20500101"

#: Dashboard range key -> how many days back the kline ``beg`` starts.
#: Slightly wider than the exact range so the requested window is always
#: covered (EastMoney clips to available trading days).
EM_RANGE_DAYS = {"1M": 31, "3M": 92, "6M": 183, "1Y": 366, "5Y": 1831}


class EastMoneyError(RuntimeError):
    """Upstream (EastMoney) failure with a user-presentable message.

    ``retryable`` is informational for this client: the Yahoo-first
    orchestration (see :mod:`hk_dashboard.providers`) decides whether to
    fall back to EastMoney, and a failed EastMoney call is never retried.
    """

    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class EastMoneySymbolNotFoundError(EastMoneyError):
    """The symbol is not known to EastMoney."""

    def __init__(self, message: str = "Symbol not found on EastMoney"):
        super().__init__(message, status=404)


def hk_em_code(symbol: str) -> str:
    """Normalize an HKEX symbol to EastMoney's zero-padded 5-digit code.

    ``0700.HK`` -> ``00700``, ``0011.HK`` -> ``00011``, ``12345.HK`` ->
    ``12345``. Any accepted input form (``700``, ``0700`` ...) is normalized
    first via :func:`hk_dashboard.symbols.normalize_symbol`.
    """
    norm = symbols.normalize_symbol(symbol)
    return norm[:-3].zfill(5)


def build_quote_url(symbol: str) -> str:
    """Build the hardcoded EastMoney quote URL for a canonical HK symbol."""
    return "%s%s?secid=%s.%s&fields=%s&ut=%s" % (
        QUOTE_BASE, QUOTE_PATH, HK_MARKET_ID, hk_em_code(symbol),
        QUOTE_FIELDS, UT_TOKEN,
    )


def build_kline_url(symbol: str, range_key: str,
                    now: _dt.datetime | None = None) -> str:
    """Build the hardcoded EastMoney daily-kline URL for a range.

    ``beg`` is derived from *now* (UTC, default) minus the range's lookback
    window; ``end`` is the fixed far-future date ``20500101``.
    """
    if not isinstance(range_key, str):
        raise InvalidRangeError(range_key)
    key = range_key.strip().upper()
    if key not in EM_RANGE_DAYS:
        raise InvalidRangeError(range_key)
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    beg = (now.astimezone(_dt.timezone.utc)
           - _dt.timedelta(days=EM_RANGE_DAYS[key])).strftime("%Y%m%d")
    return "%s%s?secid=%s.%s&klt=101&fqt=1&beg=%s&end=%s&fields1=%s&fields2=%s&ut=%s&rtntype=6" % (
        HISTORY_BASE, KLINE_PATH, HK_MARKET_ID, hk_em_code(symbol),
        beg, KLINE_END, KLINE_FIELDS1, KLINE_FIELDS2, UT_TOKEN,
    )


def _assert_allowed_em_url(url: str) -> None:
    """Reject any URL not on one of the exact allowlisted HTTPS hosts.

    Scheme+host are checked on the parsed ``netloc`` (exact match), so
    lookalike hosts (``https://push2.eastmoney.com.evil.com``), non-HTTPS
    schemes, and userinfo tricks (``https://push2.eastmoney.com@evil.com``)
    are all rejected.
    """
    if not isinstance(url, str):
        raise ValueError("Refusing to fetch a URL outside the allowed EastMoney hosts")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.netloc not in ALLOWED_EM_HOSTS:
        raise ValueError("Refusing to fetch a URL outside the allowed EastMoney hosts")


def _allowed_final_url(url: str) -> bool:
    """True if a (post-redirect) URL is on an allowlisted HTTPS host."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.netloc in ALLOWED_EM_HOSTS


def _is_retryable_http_status(code: int) -> bool:
    """True for statuses where a retry may succeed (informational here)."""
    return code == 429 or 500 <= code <= 599


def _fetch_once(url: str, timeout: float) -> dict:
    """Perform a single upstream request and decode its JSON body.

    Raises:
        EastMoneySymbolNotFoundError: on HTTP 404.
        EastMoneyError: on any other HTTP/transport/malformed-data failure,
            with ``retryable`` set for HTTP 429 / 5xx / transport errors.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = resp.geturl()
            if not _allowed_final_url(final_url):
                # The allowlisted host redirected somewhere else: refuse to
                # serve the body rather than follow an unvouched redirect.
                raise EastMoneyError(
                    "EastMoney redirected to a non-allowlisted host: %s" % final_url
                )
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise EastMoneySymbolNotFoundError(
                "Symbol not found on EastMoney (HTTP 404)"
            ) from exc
        raise EastMoneyError(
            "EastMoney returned HTTP %s" % exc.code,
            status=exc.code,
            retryable=_is_retryable_http_status(exc.code),
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise EastMoneyError(
            "Could not reach EastMoney: %s" % reason, retryable=True
        ) from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # Malformed payloads are NOT retried: a different host's response
        # would mask the validation problem instead of surfacing it.
        raise EastMoneyError("EastMoney returned a malformed response") from exc
    if not isinstance(data, dict):
        raise EastMoneyError("EastMoney returned a malformed response")
    return data


RETRY_DELAY = 1.0


def fetch_json(url: str, timeout: float = DEFAULT_TIMEOUT,
               retry_delay: float = RETRY_DELAY) -> dict:
    """Fetch and JSON-decode a response from an allowed EastMoney URL.

    Any 302 redirect to the allowlisted delayed feed is followed and
    re-validated. Retryable failures (HTTP 429, HTTP 5xx, transport errors
    — observed intermittently from some networks) are retried **at most
    once**, after a short delay, against the same URL. Nothing else is
    retried: 404, other HTTP errors, and malformed responses surface
    immediately.

    Raises:
        ValueError: if the URL is not under an allowed EastMoney host.
        EastMoneySymbolNotFoundError: on HTTP 404.
        EastMoneyError: on any other HTTP/transport/malformed-data failure
            (after at most one retry of a retryable failure).
    """
    _assert_allowed_em_url(url)
    try:
        return _fetch_once(url, timeout)
    except EastMoneyError as exc:
        if not exc.retryable:
            raise
        time.sleep(retry_delay)
        return _fetch_once(url, timeout)


def _num(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_utc(ts) -> str | None:
    if not ts:
        return None
    try:
        return _dt.datetime.fromtimestamp(int(ts), _dt.timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    except (ValueError, OverflowError, OSError):
        return None


def get_quote(raw_symbol, fetch_fn=fetch_json, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Fetch a (delayed) quote for a HKEX symbol from EastMoney.

    Field mapping (``data`` block): ``f43`` current price, ``f44`` day
    high, ``f45`` day low, ``f46`` open, ``f60`` previous close — all scaled
    by ``f59`` (decimal precision, i.e. divided by ``10 ** f59``); ``f170``
    is the % change in basis points (``f170 / 100``); ``f47`` volume;
    ``f86`` last-trade epoch (seconds); ``f57`` code; ``f58`` name.
    """
    symbol = symbols.normalize_symbol(raw_symbol)
    payload = fetch_fn(build_quote_url(symbol), timeout)
    if not isinstance(payload, dict):
        raise EastMoneyError("EastMoney returned a malformed response")
    data = payload.get("data")
    if not isinstance(data, dict) or not data:
        raise EastMoneySymbolNotFoundError(
            "Symbol not found on EastMoney (rc=%s)" % payload.get("rc")
        )
    try:
        div = 10.0 ** int(data.get("f59"))
    except (TypeError, ValueError):
        div = 1000.0  # HKEX convention: 3 decimal places

    def scaled(field):
        v = _num(data.get(field))
        return None if v is None else v / div

    price = scaled("f43")
    day_high = scaled("f44")
    day_low = scaled("f45")
    day_open = scaled("f46")
    prev_close = scaled("f60")
    if price is None:
        raise EastMoneyError("EastMoney returned no quote data for %s" % symbol)
    volume = _num(data.get("f47"))
    last_ts = _num(data.get("f86"))
    change = None
    pct = None
    if prev_close:
        change = round(price - prev_close, 4)
        pct = round((price - prev_close) / prev_close * 100.0, 2)
    else:
        p = _num(data.get("f170"))
        if p is not None:
            pct = round(p / 100.0, 2)
    return {
        "symbol": symbol,
        "name": data.get("f58") or symbol,
        "currency": "HKD",
        "exchange": "HKEX",
        "price": price,
        "previousClose": prev_close,
        "change": change,
        "pctChange": pct,
        "open": day_open,
        "dayHigh": day_high,
        "dayLow": day_low,
        "volume": int(volume) if volume is not None else None,
        "marketState": hk_market_state(),
        "lastUpdated": int(last_ts) if last_ts is not None else None,
        "lastUpdatedIso": _iso_utc(int(last_ts) if last_ts is not None else None),
        "timezone": "HKT",
        "source": "EastMoney (push2 quote API, unauthenticated)",
        "disclaimer": "Delayed quote; not guaranteed to be real-time.",
    }


def get_history(raw_symbol, range_key: str = "6M", fetch_fn=fetch_json,
                timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Fetch historical daily OHLCV bars for a HKEX symbol from EastMoney.

    Kline rows are CSV strings ordered as
    ``date,open,close,high,low,volume,...`` (already exchange-local dates,
    no timezone conversion needed).

    Args:
        raw_symbol: any accepted HKEX code form (see ``normalize_symbol``).
        range_key: one of 1M/3M/6M/1Y/5Y (case-insensitive).
    """
    symbol = symbols.normalize_symbol(raw_symbol)
    if not isinstance(range_key, str):
        raise InvalidRangeError(range_key)
    key = range_key.strip().upper()
    if key not in RANGES:
        raise InvalidRangeError(range_key)

    payload = fetch_fn(build_kline_url(symbol, key), timeout)
    if not isinstance(payload, dict):
        raise EastMoneyError("EastMoney returned a malformed response")
    data = payload.get("data") or {}
    klines = data.get("klines") or []
    rows = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 6:
            continue
        o = _num(parts[1])
        c = _num(parts[2])
        h = _num(parts[3])
        l = _num(parts[4])
        v = _num(parts[5])
        if c is None and o is None and h is None and l is None:
            continue
        rows.append(
            {
                "date": parts[0],
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": int(v) if v is not None else None,
            }
        )
    if not rows:
        raise EastMoneySymbolNotFoundError(
            "Symbol not found on EastMoney (no kline data)"
        )
    return {
        "symbol": symbol,
        "range": key,
        "rows": rows,
        "source": "EastMoney (push2his daily kline API, unauthenticated)",
        "disclaimer": "Historical daily data; may be delayed or subject to revision.",
    }
