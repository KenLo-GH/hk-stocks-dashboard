"""Minimal Yahoo Finance client (unauthenticated v8 chart endpoint).

Only the hosts in :data:`ALLOWED_UPSTREAM_BASES` are ever contacted:
``query1.finance.yahoo.com`` (primary) and ``query2.finance.yahoo.com``
(fallback, tried at most once on retryable failures). Chart URLs are built
from a hardcoded base path plus a server-validated/normalized symbol and a
whitelisted range key, so user input can never steer requests to an
arbitrary upstream URL.
"""

from __future__ import annotations

import datetime as _dt
import json
import urllib.error
import urllib.parse
import urllib.request

from . import symbols

__all__ = [
    "ALLOWED_UPSTREAM_BASES",
    "PRIMARY_UPSTREAM_BASE",
    "FALLBACK_UPSTREAM_BASE",
    "CHART_PATH",
    "RANGES",
    "USER_AGENT",
    "DEFAULT_TIMEOUT",
    "YahooHTTPError",
    "SymbolNotFoundError",
    "InvalidRangeError",
    "hk_market_state",
    "build_chart_url",
    "fetch_json",
    "get_quote",
    "get_history",
]

CHART_PATH = "/v8/finance/chart/"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 HKStockDashboard/1.0"
)
DEFAULT_TIMEOUT = 10.0
HKG_OFFSET = 9 * 3600  # Hong Kong time, UTC+09:00

#: Dashboard range key -> Yahoo ``range=`` parameter value.
RANGES = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "5Y": "5y"}

#: The only upstream hosts this app will ever talk to (primary + fallback).
ALLOWED_UPSTREAM_BASES = (
    "https://query1.finance.yahoo.com",
    "https://query2.finance.yahoo.com",
)

#: Primary upstream host, always tried first.
PRIMARY_UPSTREAM_BASE = ALLOWED_UPSTREAM_BASES[0]

#: Same-service fallback host. Tried at most once, and only when the primary
#: host fails with a retryable error (HTTP 429, HTTP 5xx, transport error).
FALLBACK_UPSTREAM_BASE = ALLOWED_UPSTREAM_BASES[1]

# HKEX regular trading sessions in seconds-of-day (HKT): 09:30-12:00, 13:00-16:00.
_HK_SESSIONS = ((9 * 3600 + 30 * 60, 12 * 3600), (13 * 3600, 16 * 3600))


class YahooHTTPError(RuntimeError):
    """Upstream (Yahoo Finance) failure with a user-presentable message.

    ``retryable`` marks failures where retrying the identical request on the
    sibling Yahoo host (query2) may succeed (HTTP 429, HTTP 5xx, and
    transport/network errors). It is set by :func:`fetch_json`; other
    callers may treat it as informational.
    """

    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class SymbolNotFoundError(YahooHTTPError):
    """The symbol is not known to Yahoo Finance."""

    def __init__(self, message: str = "Symbol not found on Yahoo Finance"):
        super().__init__(message, status=404)


class InvalidRangeError(ValueError):
    """The requested history range is not one of the supported keys."""

    def __init__(self, range_key):
        super().__init__(
            "Unsupported range %r; use one of: %s" % (range_key, ", ".join(RANGES))
        )
        self.range_key = range_key


def build_chart_url(symbol: str, yf_range: str) -> str:
    """Build a chart API URL.

    ``symbol`` must already be a canonical Yahoo HK symbol (validated again
    here as defense in depth) and ``yf_range`` must be an alphanumeric value
    coming from the :data:`RANGES` whitelist.
    """
    if not isinstance(symbol, str):
        raise symbols.SymbolError("Symbol is required")
    if symbols.normalize_symbol(symbol) != symbol:
        raise symbols.SymbolError("build_chart_url expects a normalized symbol")
    if not yf_range or not yf_range.isascii() or not yf_range.isalnum():
        raise ValueError("invalid upstream range value")
    return (
        PRIMARY_UPSTREAM_BASE
        + CHART_PATH
        + symbol
        + "?range="
        + yf_range
        + "&interval=1d&events=div%2Csplit"
    )


def hk_market_state(now_utc: _dt.datetime | None = None, gmtoffset: int = HKG_OFFSET) -> str:
    """Approximate HKEX market state: ``"open"`` or ``"closed"``.

    Based on local time only (Mon-Fri, 09:30-12:00 and 13:00-16:00 in the
    exchange timezone). HK public holidays are NOT accounted for, so the
    state may be wrong on holidays.
    """
    if now_utc is None:
        now_utc = _dt.datetime.now(_dt.timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=_dt.timezone.utc)
    local = now_utc.astimezone(_dt.timezone.utc) + _dt.timedelta(seconds=gmtoffset)
    if local.weekday() >= 5:
        return "closed"
    secs = local.hour * 3600 + local.minute * 60 + local.second
    for start, end in _HK_SESSIONS:
        if start <= secs < end:
            return "open"
    return "closed"


def _is_retryable_http_status(code: int) -> bool:
    """True for statuses where a retry on the sibling Yahoo host may succeed."""
    return code == 429 or 500 <= code <= 599


def _assert_allowed_upstream(url: str) -> None:
    """Reject any URL not under one of the exact allowlisted HTTPS hosts.

    The check is a scheme+host prefix match (``https://host/``), so lookalike
    hosts (``https://query1.finance.yahoo.com.evil.com``) and non-HTTPS
    schemes are rejected.
    """
    if not any(url.startswith(base + "/") for base in ALLOWED_UPSTREAM_BASES):
        raise ValueError("Refusing to fetch a URL outside the allowed upstream hosts")


def _fallback_url(url: str) -> str | None:
    """Return the identical request on the fallback host, or ``None``.

    Only a primary-host (query1) URL has a fallback. A URL already on the
    fallback host (or any other host) never retries again, so at most two
    upstream requests are ever made per ``fetch_json`` call.
    """
    if not url.startswith(PRIMARY_UPSTREAM_BASE + "/"):
        return None
    parts = urllib.parse.urlsplit(url)
    fallback_host = FALLBACK_UPSTREAM_BASE.split("://", 1)[1]
    return urllib.parse.urlunsplit(
        (parts.scheme, fallback_host, parts.path, parts.query, parts.fragment)
    )


def _fetch_once(url: str, timeout: float) -> dict:
    """Perform a single upstream request and decode its JSON body.

    Raises:
        SymbolNotFoundError: on HTTP 404 from Yahoo.
        YahooHTTPError: on any other HTTP/transport/malformed-data failure,
            with ``retryable`` set for HTTP 429 / 5xx / transport errors.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = _http_error_detail(exc)
        if exc.code == 404:
            raise SymbolNotFoundError(detail) from exc
        raise YahooHTTPError(
            detail, status=exc.code, retryable=_is_retryable_http_status(exc.code)
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise YahooHTTPError(
            "Could not reach Yahoo Finance: %s" % reason, retryable=True
        ) from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # Malformed payloads are NOT retried: a different host's response
        # would mask the validation problem instead of surfacing it.
        raise YahooHTTPError("Yahoo Finance returned a malformed response") from exc
    if not isinstance(data, dict):
        raise YahooHTTPError("Yahoo Finance returned a malformed response")
    return data


def fetch_json(url: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Fetch and JSON-decode a response from an allowed upstream URL.

    The primary host (``query1``) is tried first. If it fails with a
    retryable error (HTTP 429, HTTP 5xx, or a transport/network error), the
    identical path and query are retried **at most once** against the
    fallback host (``query2``). HTTP 404 (symbol not found), other HTTP
    errors, and malformed-data errors are raised immediately, without any
    retry. If both hosts fail, the primary host's error is raised.

    Raises:
        ValueError: if the URL is not under an allowed upstream host.
        SymbolNotFoundError: on HTTP 404 from Yahoo (never retried).
        YahooHTTPError: on any other HTTP/transport/malformed-data failure.
    """
    _assert_allowed_upstream(url)
    try:
        return _fetch_once(url, timeout)
    except SymbolNotFoundError:
        raise  # deterministic upstream answer: never retried
    except YahooHTTPError as primary_exc:
        fallback = _fallback_url(url)
        if fallback is None or not primary_exc.retryable:
            raise
        try:
            return _fetch_once(fallback, timeout)
        except SymbolNotFoundError:
            raise  # keep not-found semantics even when reached via fallback
        except YahooHTTPError:
            # Both hosts failed: report the primary host's error.
            raise primary_exc


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    """Extract Yahoo's human-readable error description, if present."""
    try:
        body = json.loads(exc.read().decode("utf-8", "replace"))
        err = (body.get("chart") or {}).get("error") or {}
        desc = err.get("description")
        if desc:
            return str(desc)
    except Exception:
        pass
    return "Yahoo Finance returned HTTP %s" % exc.code


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


def _chart_result(payload: dict) -> dict:
    """Pull the single result object out of a v8 chart payload, raising on errors."""
    chart = payload.get("chart") or {}
    err = chart.get("error")
    if err:
        desc = str(err.get("description") or "Unknown error from Yahoo Finance")
        code = str(err.get("code") or "")
        if code == "Not Found" or "no data found" in desc.lower() or "unknown symbol" in desc.lower():
            raise SymbolNotFoundError(desc)
        raise YahooHTTPError(desc, status=502)
    results = chart.get("result") or []
    if not results:
        raise YahooHTTPError("Yahoo Finance returned no data")
    return results[0]


def get_quote(raw_symbol, fetch_fn=fetch_json, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Fetch a (delayed) quote for a HKEX symbol.

    The quote is derived from the ``meta`` block of the 1-day chart payload
    (this endpoint works unauthenticated; the v7 quote endpoint requires a
    crumb/cookie).
    """
    symbol = symbols.normalize_symbol(raw_symbol)
    payload = fetch_fn(build_chart_url(symbol, "1d"), timeout)
    result = _chart_result(payload)
    meta = result.get("meta") or {}
    price = _num(meta.get("regularMarketPrice"))
    prev_close = _num(meta.get("chartPreviousClose"))
    if prev_close is None:
        prev_close = _num(meta.get("previousClose"))
    change = None
    pct = None
    if price is not None and prev_close:
        change = round(price - prev_close, 4)
        pct = round((price - prev_close) / prev_close * 100.0, 2)
    gmtoffset = int(meta.get("gmtoffset") or HKG_OFFSET)
    last_ts = meta.get("regularMarketTime")
    # The 1d chart payload also carries (intraday) bars; the first bar of the
    # last session is the day's opening price.
    day_open = None
    quote_ind = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote_ind.get("open") or []
    if opens:
        day_open = _num(opens[0])
    return {
        "symbol": symbol,
        "name": meta.get("longName") or meta.get("shortName") or symbol,
        "currency": meta.get("currency") or "HKD",
        "exchange": meta.get("fullExchangeName") or meta.get("exchange") or "HKEX",
        "price": price,
        "previousClose": prev_close,
        "change": change,
        "pctChange": pct,
        "open": day_open,
        "dayHigh": _num(meta.get("regularMarketDayHigh")),
        "dayLow": _num(meta.get("regularMarketDayLow")),
        "volume": _num(meta.get("regularMarketVolume")),
        "marketState": hk_market_state(gmtoffset=gmtoffset),
        "lastUpdated": last_ts,
        "lastUpdatedIso": _iso_utc(last_ts),
        "timezone": meta.get("exchangeTimezoneName") or "HKT",
        "source": "Yahoo Finance v8 chart API (unauthenticated)",
        "disclaimer": "Delayed quote; not guaranteed to be real-time.",
    }


def get_history(raw_symbol, range_key: str = "6M", fetch_fn=fetch_json,
                timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Fetch historical daily OHLCV bars for a HKEX symbol.

    Args:
        raw_symbol: any accepted HKEX code form (see ``normalize_symbol``).
        range_key: one of 1M/3M/6M/1Y/5Y (case-insensitive).
    """
    symbol = symbols.normalize_symbol(raw_symbol)
    if not isinstance(range_key, str):
        raise InvalidRangeError(range_key)
    yf_range = RANGES.get(range_key.strip().upper())
    if yf_range is None:
        raise InvalidRangeError(range_key)

    payload = fetch_fn(build_chart_url(symbol, yf_range), timeout)
    result = _chart_result(payload)
    meta = result.get("meta") or {}
    gmtoffset = int(meta.get("gmtoffset") or HKG_OFFSET)
    tz = _dt.timezone(_dt.timedelta(seconds=gmtoffset))

    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]

    def _at(arr, i):
        return arr[i] if isinstance(arr, list) and i < len(arr) else None

    rows = []
    for i, ts in enumerate(stamps):
        o = _num(_at(quote.get("open"), i))
        h = _num(_at(quote.get("high"), i))
        l = _num(_at(quote.get("low"), i))
        c = _num(_at(quote.get("close"), i))
        v = _num(_at(quote.get("volume"), i))
        if c is None and o is None and h is None and l is None:
            continue
        try:
            date_str = _dt.datetime.fromtimestamp(int(ts), tz).strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            date_str = str(ts)
        rows.append(
            {
                "date": date_str,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": int(v) if v is not None else None,
            }
        )
    return {
        "symbol": symbol,
        "range": range_key.strip().upper(),
        "rows": rows,
        "source": "Yahoo Finance v8 chart API (unauthenticated, daily bars)",
        "disclaimer": "Historical daily data; may be delayed or subject to revision.",
    }
