"""Provider orchestration: Yahoo Finance first, EastMoney as final fallback.

The dashboard tries Yahoo Finance (query1 -> query2) first for both quotes
and daily history. Only when **both** Yahoo hosts fail with retryable
errors (HTTP 429, HTTP 5xx, or transport/network failure) does it fall back
to EastMoney's read-only APIs. Deterministic upstream answers — HTTP 404
(symbol not found), non-retryable HTTP errors, and malformed responses —
never trigger the EastMoney fallback; they are surfaced as-is so the user
sees the real upstream answer.

Every successful response carries a ``source`` field naming the provider
that actually served it ("Yahoo Finance ..." or "EastMoney ..."), so the
UI/API attribution stays accurate. All data is delayed and not guaranteed
to be real-time, regardless of source.
"""

from __future__ import annotations

import datetime as _dt

from . import eastmoney, yahoo

__all__ = ["get_quote", "get_history"]


def _yahoo_unavailable(exc: yahoo.YahooHTTPError) -> bool:
    """True when both Yahoo hosts failed for retryable reasons.

    This is exactly the condition under which the EastMoney fallback is
    warranted: rate-limiting (429), server errors (5xx), or
    transport/network failure after the query1 -> query2 retry.
    """
    return exc.retryable


def get_quote(raw_symbol, yahoo_fetch=None, em_fetch=None,
              timeout: float = yahoo.DEFAULT_TIMEOUT) -> dict:
    """Quote for a HKEX symbol: Yahoo first, EastMoney as final fallback.

    Args:
        raw_symbol: any accepted HKEX code form.
        yahoo_fetch: injectable Yahoo fetch (tests); default
            ``yahoo.fetch_json``.
        em_fetch: injectable EastMoney fetch (tests); default
            ``eastmoney.fetch_json``.
        timeout: upstream HTTP timeout in seconds.
    """
    symbol = yahoo.symbols.normalize_symbol(raw_symbol)
    try:
        return yahoo.get_quote(
            symbol, fetch_fn=yahoo_fetch or yahoo.fetch_json, timeout=timeout
        )
    except yahoo.SymbolNotFoundError:
        raise
    except yahoo.YahooHTTPError as exc:
        if not _yahoo_unavailable(exc):
            raise
    em_fetch = em_fetch or eastmoney.fetch_json
    return eastmoney.get_quote(symbol, fetch_fn=em_fetch, timeout=timeout)


def get_history(raw_symbol, range_key: str = "6M", yahoo_fetch=None,
                em_fetch=None, timeout: float = yahoo.DEFAULT_TIMEOUT) -> dict:
    """Daily history for a HKEX symbol: Yahoo first, EastMoney final fallback.

    Args:
        raw_symbol: any accepted HKEX code form.
        range_key: one of 1M/3M/6M/1Y/5Y (case-insensitive).
        yahoo_fetch: injectable Yahoo fetch (tests); default
            ``yahoo.fetch_json``.
        em_fetch: injectable EastMoney fetch (tests); default
            ``eastmoney.fetch_json``.
        timeout: upstream HTTP timeout in seconds.
    """
    symbol = yahoo.symbols.normalize_symbol(raw_symbol)
    # Validate the range up front so a bad key is 400 regardless of provider.
    if not isinstance(range_key, str):
        raise yahoo.InvalidRangeError(range_key)
    if range_key.strip().upper() not in yahoo.RANGES:
        raise yahoo.InvalidRangeError(range_key)
    try:
        return yahoo.get_history(
            symbol, range_key, fetch_fn=yahoo_fetch or yahoo.fetch_json,
            timeout=timeout,
        )
    except yahoo.SymbolNotFoundError:
        raise
    except yahoo.YahooHTTPError as exc:
        if not _yahoo_unavailable(exc):
            raise
    em_fetch = em_fetch or eastmoney.fetch_json
    return eastmoney.get_history(symbol, range_key.strip().upper(),
                                 fetch_fn=em_fetch, timeout=timeout)


def provider_sources() -> tuple:
    """(primary, fallback) source labels for display/README accuracy."""
    return (
        "Yahoo Finance v8 chart API (unauthenticated)",
        "EastMoney (unauthenticated, read-only fallback)",
    )


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)
