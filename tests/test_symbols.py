"""Unit tests for HKEX symbol normalization."""

import unittest

from hk_dashboard.symbols import SymbolError, normalize_symbol


class TestNormalizeSymbol(unittest.TestCase):
    def test_short_code_zero_padded(self):
        self.assertEqual(normalize_symbol("700"), "0700.HK")

    def test_four_digit_code(self):
        self.assertEqual(normalize_symbol("0700"), "0700.HK")

    def test_already_canonical_unchanged(self):
        self.assertEqual(normalize_symbol("0700.HK"), "0700.HK")
        self.assertEqual(normalize_symbol("9988.HK"), "9988.HK")

    def test_five_digit_code(self):
        self.assertEqual(normalize_symbol("12345"), "12345.HK")
        self.assertEqual(normalize_symbol("12345.HK"), "12345.HK")

    def test_lowercase_suffix_and_whitespace(self):
        self.assertEqual(normalize_symbol("0700.hk"), "0700.HK")
        self.assertEqual(normalize_symbol("  700 "), "0700.HK")
        self.assertEqual(normalize_symbol(" 0700.HK \n"), "0700.HK")

    def test_numeric_string(self):
        self.assertEqual(normalize_symbol(700), "0700.HK")

    def test_invalid_empty(self):
        for bad in ("", "   ", None, "abc", "0700.US", "0700.hkx",
                    "123456", "700..HK", "-700", "0x700", "HK700", "0700 HK"):
            with self.assertRaises(SymbolError, msg=repr(bad)):
                normalize_symbol(bad)


if __name__ == "__main__":
    unittest.main()
