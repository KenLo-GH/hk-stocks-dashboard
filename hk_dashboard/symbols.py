"""Normalization and validation of HKEX stock symbols.

Accepts human-friendly forms (``700``, ``0700``, ``0700.HK``, ...) and
normalizes them to the 4/5-digit, ``.HK``-suffixed form that Yahoo Finance
uses (``0700.HK``). Already-valid forms pass through unchanged.
"""

from __future__ import annotations

import re

__all__ = ["SymbolError", "normalize_symbol"]

_NUMERIC_1_5 = re.compile(r"^\d{1,5}$")


class SymbolError(ValueError):
    """Raised when input cannot be normalized to a valid HKEX Yahoo symbol."""


def normalize_symbol(raw) -> str:
    """Normalize *raw* into a canonical Yahoo Finance HK symbol.

    Accepted forms: ``700``, ``0700``, ``700.HK``, ``0700.HK``, ``12345`` ...
    (1-5 digits, optional ``.HK`` suffix, case-insensitive, surrounding
    whitespace allowed). The numeric part is zero-padded on the left to at
    least 4 digits. Already-canonical input (e.g. ``0700.HK``) is returned
    unchanged.

    Raises:
        SymbolError: for anything that is not a plausible HKEX code.
    """
    if raw is None:
        raise SymbolError("Symbol is required")
    text = str(raw).strip().upper()
    if not text:
        raise SymbolError("Symbol is required")

    if "." in text:
        base, _, suffix = text.rpartition(".")
        if suffix != "HK":
            raise SymbolError(
                "Unsupported exchange suffix - use an HKEX code like 700, 0700 or 0700.HK"
            )
        digits = base
    else:
        digits = text

    if not _NUMERIC_1_5.match(digits):
        raise SymbolError(
            "Symbol must be 1-5 digits, optionally followed by .HK (e.g. 700 or 0700.HK)"
        )
    if len(digits) < 4:
        digits = digits.zfill(4)
    return digits + ".HK"
