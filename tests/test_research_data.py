"""Tests for the 1596.HK September 2026 research dataset integration.

Verifies:
* the validated CSVs exist under static/data/01596/ and are byte-identical
  to the source copies in data/01596/ (sha256);
* the FastAPI (Vercel) app serves them at /static/data/01596/* with 200 +
  the right content type, and 404s for missing files;
* index.html carries the required UI structure and labels (tabs, date
  filter, pager, record count, download links, side legend, the
  "only Sep 24 file contains individual trades / Sep 1-23 ticks not
  available for free" statement, and that bars are not transactions);
* the zero-volume filter: index.html carries the accessible checkbox
  markup and the "Downloads include all rows." note, app.js wires the
  state/filter/toggle, and the actual filtering rule is exercised against
  the real CSVs (161 non-zero / 1169 total);
* app.js wires the section (research module strings present);
* the shipped CSVs themselves parse with the expected row counts and
  contents (18 daily rows, 1169 five-minute rows, 232 one-minute rows,
  9 tick rows, side values in {1, 2}).

No network is used: the upstream Yahoo/EastMoney fetchers are patched to
the canned fakes from tests/fakes.py.
"""

from __future__ import annotations

import csv
import hashlib
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as vercel_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from hk_dashboard import eastmoney, yahoo  # noqa: E402
from tests.fakes import FakeUpstream, make_chart_payload, make_em_quote_payload  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATIC_RESEARCH = ROOT / "static" / "data" / "01596"
SOURCE_RESEARCH = ROOT / "data" / "01596"

CSV_FILES = (
    "01596_daily_ohlc.csv",
    "01596_5min_bars.csv",
    "01596_1min_bars_today.csv",
    "01596_ticks_today_20260924.csv",
)


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _read_csv(name: str) -> list[dict]:
    with (STATIC_RESEARCH / name).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class ResearchStaticFiles(unittest.TestCase):
    """The static dataset: presence, integrity vs. source, row counts."""

    def test_csv_files_exist_in_static(self):
        for name in CSV_FILES:
            p = STATIC_RESEARCH / name
            self.assertTrue(p.is_file(), msg=name)
            self.assertGreater(p.stat().st_size, 0, msg=name)

    def test_csv_files_are_byte_identical_to_source(self):
        for name in CSV_FILES:
            self.assertEqual(
                _sha256(STATIC_RESEARCH / name),
                _sha256(SOURCE_RESEARCH / name),
                msg=name,
            )

    def test_sources_note_exists(self):
        p = STATIC_RESEARCH / "sources.txt"
        self.assertTrue(p.is_file())
        text = p.read_text(encoding="utf-8")
        self.assertIn("sell-initiated", text)
        self.assertIn("buy-initiated", text)
        self.assertIn("NOT available", text)

    def test_daily_rows(self):
        rows = _read_csv("01596_daily_ohlc.csv")
        self.assertEqual(len(rows), 18)
        self.assertEqual(rows[0]["datetime"], "2026-09-01")
        self.assertEqual(rows[-1]["datetime"], "2026-09-24")
        for r in rows:
            self.assertTrue(
                "2026-09-01" <= r["datetime"] <= "2026-09-24", msg=r["datetime"]
            )
            for col in ("open", "high", "low", "close", "volume", "turnover_hkd"):
                float(r[col])  # numeric

    def test_5min_rows(self):
        rows = _read_csv("01596_5min_bars.csv")
        self.assertEqual(len(rows), 1169)
        self.assertEqual(rows[0]["datetime"], "2026-09-01 09:35")
        self.assertEqual(rows[-1]["datetime"], "2026-09-24 14:25")
        dates = sorted({r["datetime"][:10] for r in rows})
        self.assertEqual(len(dates), 18)
        self.assertEqual(dates[0], "2026-09-01")
        self.assertEqual(dates[-1], "2026-09-24")

    def test_1min_rows_today_only(self):
        rows = _read_csv("01596_1min_bars_today.csv")
        self.assertEqual(len(rows), 232)
        for r in rows:
            self.assertTrue(r["datetime"].startswith("2026-09-24 "))

    def test_ticks_rows_and_side_legend(self):
        rows = _read_csv("01596_ticks_today_20260924.csv")
        self.assertEqual(len(rows), 9)
        self.assertEqual(rows[0]["time"], "10:22:18")
        self.assertEqual(rows[-1]["time"], "14:00:25")
        for r in rows:
            self.assertIn(r["side"], ("1", "2"))  # 1 sell-initiated, 2 buy-initiated
            float(r["price"])
            int(r["volume"])


class ResearchRoutes(unittest.TestCase):
    """Vercel (FastAPI) routes for the research data files and index.html."""

    def setUp(self):
        self.yfake = FakeUpstream(make_chart_payload())
        self.emfake = FakeUpstream(make_em_quote_payload())
        self._orig_y = yahoo.fetch_json
        self._orig_e = eastmoney.fetch_json
        yahoo.fetch_json = self.yfake
        eastmoney.fetch_json = self.emfake
        self.client = TestClient(vercel_app.app)

    def tearDown(self):
        yahoo.fetch_json = self._orig_y
        eastmoney.fetch_json = self._orig_e

    # ---- static dataset routes -------------------------------------------
    def test_research_csv_routes_served(self):
        for name in CSV_FILES:
            r = self.client.get("/static/data/01596/" + name)
            self.assertEqual(r.status_code, 200, msg=name)
            self.assertIn("csv", r.headers.get("content-type", "").lower(), msg=name)
            self.assertGreater(len(r.content), 0, msg=name)

    def test_research_sources_note_served(self):
        r = self.client.get("/static/data/01596/sources.txt")
        self.assertEqual(r.status_code, 200)
        self.assertIn("sell-initiated", r.text)

    def test_research_missing_file_404(self):
        r = self.client.get("/static/data/01596/missing.csv")
        self.assertEqual(r.status_code, 404)

    def test_research_path_traversal_blocked(self):
        r = self.client.get("/static/data/01596/../../app.py")
        self.assertEqual(r.status_code, 404)

    # ---- index.html UI text ----------------------------------------------
    def _index(self) -> str:
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        return r.text

    def test_research_section_prominent_and_labeled(self):
        html = self._index()
        self.assertIn('id="research-panel"', html)
        self.assertIn("1596.HK · September 2026 research data", html)
        self.assertIn("research dataset", html)
        # Clearly reachable from anywhere on the page:
        self.assertIn('href="#research-panel"', html)
        self.assertIn("1596.HK · September 2026 research data ↓", html)

    def test_research_tabs(self):
        html = self._index()
        self.assertIn('role="tablist"', html)
        self.assertIn('data-rtab="daily"', html)
        self.assertIn('data-rtab="bars5"', html)
        self.assertIn('data-rtab="ticks"', html)
        self.assertIn(">Daily</button>", html)
        self.assertIn(">5-minute</button>", html)
        self.assertIn("Sep 24 individual trades", html)
        self.assertIn('aria-controls="r-panel"', html)

    def test_research_date_filter_and_pager_and_count(self):
        html = self._index()
        self.assertIn('id="r5-date"', html)          # date filter (5-min tab)
        self.assertIn("Date (5-minute bars)", html)
        self.assertIn("All dates (Sep 1–24)", html)
        self.assertIn('id="r-prev"', html)
        self.assertIn('id="r-next"', html)
        self.assertIn('id="r-pageinfo"', html)
        self.assertIn('id="r-count"', html)
        self.assertIn('aria-live="polite"', html)

    def test_research_download_links(self):
        html = self._index()
        for name in CSV_FILES:
            self.assertIn('/static/data/01596/' + name, html)
        # every data file is offered as a download link
        for name in CSV_FILES:
            self.assertIn(
                'href="/static/data/01596/' + name + '" download', html, msg=name
            )
        self.assertIn("Download CSV", html)
        self.assertIn("/static/data/01596/sources.txt", html)

    def test_research_states(self):
        html = self._index()
        self.assertIn('id="r-state"', html)   # loading/error/empty state line
        self.assertIn('id="r-retry"', html)   # error state: retry

    def test_research_honest_labels(self):
        html = self._index()
        # Only Sep 24 file contains genuine individual trades.
        self.assertIn("only the <strong>Sep 24</strong> file contains genuine individual transactions", html)
        # Past ticks unavailable for free.
        self.assertIn("unavailable from free sources", html)
        self.assertIn("Sep 1–23", html)
        # Bars must not be mislabeled as transactions.
        self.assertIn("aggregated bars, not individual transactions", html)
        # Side legend.
        self.assertIn("<code>1</code> = sell-initiated", html)
        self.assertIn("<code>2</code> = buy-initiated", html)
        self.assertIn("<code>4</code> = neutral", html)

    def test_research_hide_zero_checkbox_markup(self):
        html = self._index()
        # Accessible checkbox beside the 5-minute date filter:
        self.assertIn('<input type="checkbox" id="research-hide-zero"', html)
        self.assertIn('for="research-hide-zero"', html)
        self.assertIn("Hide zero-volume rows", html)
        # Checked by default.
        self.assertIn(
            '<input type="checkbox" id="research-hide-zero" checked', html
        )
        # The label sits inside the r5-filter row that is only shown on the
        # 5-minute tab (app.js toggles #r5-filter.hidden per tab), before
        # the record count:
        self.assertIn('id="r5-filter"', html)
        i_filter = html.index('id="r5-filter"')
        i_label = html.index('for="research-hide-zero"')
        self.assertGreater(i_label, i_filter)
        self.assertLess(i_label, html.index('id="r-count"'))
        # The download note makes clear the CSVs are never filtered:
        self.assertIn("Downloads include all rows.", html)

    def test_research_hide_zero_wiring(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        for needle in (
            'LS_HIDE_ZERO_KEY = "hk-dashboard.research-hide-zero"',
            "hideZero: true",
            'rEls.hideZero = $("research-hide-zero")',
            "loadHideZeroPref",
            "saveHideZeroPref",
            "Number(b.volume) !== 0",
            'rEls.hideZero.checked = RESEARCH.hideZero = loadHideZeroPref()',
            'RESEARCH.hideZero = rEls.hideZero.checked',
        ):
            self.assertIn(needle, js, msg=needle)
        # The toggle resets to page 1 and re-renders, but does not reset the
        # selected date.
        self.assertIn("RESEARCH.page = 1; // the selected date is preserved", js)
        # Only the 5-minute (bars5) rows pass through the volume filter;
        # daily and ticks rows return before it.
        i_daily = js.index('if (tab === "daily") return RESEARCH.data.daily;')
        i_ticks = js.index('if (tab === "ticks") return RESEARCH.data.ticks;')
        i_filter = js.index("Number(b.volume) !== 0")
        self.assertLess(i_daily, i_filter)
        self.assertLess(i_ticks, i_filter)

    def test_research_hide_zero_css(self):
        css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".hide-zero-label", css)
        # Mobile: the filter row must stay usable on small screens - the
        # .hide-zero-label rule must sit inside a 640px media block.
        marker = "@media (max-width: 640px)"
        self.assertIn(marker, css)
        # the research panel rules live in the file's last 640px block
        block = css[css.rindex(marker): css.rindex(marker) + 400]
        self.assertIn(".hide-zero-label", block, msg="hide-zero rule inside 640px media block")

    def test_research_hide_zero_filter_logic(self):
        """Exercise the actual filtering rule against the real CSV rows."""
        rows = _read_csv("01596_5min_bars.csv")

        # The exact predicate used by app.js (String volume -> Number):
        keep = [r for r in rows if float(r["volume"]) != 0]
        self.assertEqual(len(rows), 1169)
        self.assertEqual(len(keep), 161)
        self.assertEqual(len(rows) - len(keep), 1008)
        for r in keep:
            self.assertNotEqual(float(r["volume"]), 0)

        # Date filter composed with hide-zero, as researchRowsFor does it:
        def rows_for(date: str, hide_zero: bool) -> list[dict]:
            out = [
                r for r in rows
                if date == "all" or r["datetime"][:10] == date
            ]
            if hide_zero:
                out = [r for r in out if float(r["volume"]) != 0]
            return out

        self.assertEqual(len(rows_for("all", True)), 161)
        self.assertEqual(len(rows_for("all", False)), 1169)
        self.assertEqual(len(rows_for("2026-09-24", False)), 47)
        self.assertEqual(len(rows_for("2026-09-24", True)), 5)
        # Page counts the UI would derive (pageSize = 50, newest first):
        self.assertEqual((len(rows_for("all", True)) + 49) // 50, 4)
        self.assertEqual((len(rows_for("all", False)) + 49) // 50, 24)

        # Genuine transaction rows are never touched by the filter:
        ticks = _read_csv("01596_ticks_today_20260924.csv")
        self.assertEqual(len(ticks), 9)
        self.assertEqual(len([t for t in ticks if float(t["volume"]) != 0]), 9)

    def test_research_accessibility_and_mobile(self):
        html = self._index()
        css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn('aria-label="1596.HK September 2026 research data"', html)
        self.assertIn('aria-labelledby="r-tab-daily"', html)
        self.assertIn("<caption class=\"visually-hidden\">", html)
        self.assertIn("role=\"status\"", html)
        self.assertIn(".research-panel", css)
        self.assertIn("@media (max-width: 640px)", css)
        self.assertIn(".research-panel { padding: 14px; }", css)


class AppJsResearchWiring(unittest.TestCase):
    def test_app_js_has_research_module(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        for needle in (
            "1596.HK Sep-2026 research data",
            "01596_daily_ohlc.csv",
            "01596_5min_bars.csv",
            "01596_ticks_today_20260924.csv",
            "research-panel",
            "r5-date",
            "parseCsv",
            "updateResearchFor",
            "Loading 1596.HK September 2026 research data",
            "Failed to load research data",
            "No five-minute bars recorded for",
            "research-hide-zero",
            "sell-initiated",
            "buy-initiated",
            "1596.HK",
        ):
            self.assertIn(needle, js, msg=needle)
        # The module is actually invoked on startup:
        self.assertIn("initResearch();", js)


class AppJsHideZeroScript(unittest.TestCase):
    def test_hide_zero_jsdom_script_present(self):
        p = ROOT / "tests" / "test_hide_zero_frontend.js"
        self.assertTrue(p.is_file(), "jsdom functional script must ship")
        text = p.read_text(encoding="utf-8")
        for needle in (
            "research-hide-zero",
            "Hide zero-volume rows",
            "Downloads include all rows.",
            # behaviour-level checks the script performs:
            '"Page 1 of 4"',
            '"Page 1 of 24"',
            "zero-volume rows hidden",
            "selected date is preserved",
        ):
            self.assertIn(needle, text, msg=needle)

    def test_hide_zero_jsdom_script_runs_when_node_available(self):
        """Run the real jsdom test (real HTML + JS + real CSVs) when node is
        on PATH; skipped otherwise so the suite stays green on minimal
        images."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node not on PATH")
        proc = subprocess.run(
            [str(node), str(ROOT / "tests" / "test_hide_zero_frontend.js")],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            proc.returncode, 0,
            msg="stdout: %s\nstderr: %s" % (proc.stdout, proc.stderr),
        )
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
