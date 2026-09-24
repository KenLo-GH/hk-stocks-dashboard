/* Functional test: "Hide zero-volume rows" filter for the 1596.HK research
 * panel. Boots the real static/index.html + static/app.js in jsdom with the
 * real CSVs and verifies the filter end-to-end:
 *   - checkbox markup, default checked;
 *   - default view on the 5-minute tab shows only non-zero-volume bars;
 *   - toggling off shows all bars and resets to page 1;
 *   - the selected date filter is preserved across the toggle;
 *   - record/page counts update;
 *   - genuine transaction (ticks) rows are never filtered;
 *   - downloads still point at the original, unfiltered CSVs;
 *   - the preference is persisted (and safely restored) via localStorage.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { JSDOM } = require(path.join(__dirname, "..", "node_modules", "jsdom"));

const ROOT = path.join(__dirname, "..");
const DATA_DIR = path.join(ROOT, "static", "data", "01596");
const CSV_FILES = [
  "01596_daily_ohlc.csv",
  "01596_5min_bars.csv",
  "01596_1min_bars_today.csv",
  "01596_ticks_today_20260924.csv",
];

let failures = 0;
function assert(cond, msg) {
  if (!cond) {
    failures += 1;
    console.error("ASSERTION FAILED: " + msg);
  }
}

function makeFetch() {
  return function fetch(url) {
    const base = String(url).split("?")[0];
    for (const name of CSV_FILES) {
      const p = "/static/data/01596/" + name;
      if (base === p) {
        const text = fs.readFileSync(path.join(DATA_DIR, name), "utf8");
        return Promise.resolve({
          ok: true,
          status: 200,
          text: () => Promise.resolve(text),
          json: () => Promise.resolve({}),
        });
      }
    }
    return Promise.resolve({
      ok: false,
      status: 599,
      text: () => Promise.resolve(""),
      json: () => Promise.resolve({ error: "stubbed endpoint" }),
    });
  };
}

function bootDom(preloadPrefs) {
  const html = fs.readFileSync(path.join(ROOT, "static", "index.html"), "utf8");
  const appJs = fs.readFileSync(path.join(ROOT, "static", "app.js"), "utf8");
  const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only" });
  dom.window.fetch = makeFetch();
  for (const k of Object.keys(preloadPrefs)) {
    dom.window.localStorage.setItem(k, preloadPrefs[k]);
  }
  dom.window.eval(appJs);
  return dom;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async function main() {
  const dom = bootDom({});
  const doc = dom.window.document;

  // open the research panel by loading the 1596.HK symbol
  doc.getElementById("symbol-input").value = "1596";
  doc.getElementById("search-form").dispatchEvent(
    new dom.window.Event("submit", { bubbles: true, cancelable: true })
  );
  await sleep(250);

  // --- markup ---------------------------------------------------------------
  const cb = doc.getElementById("research-hide-zero");
  assert(cb, "checkbox #research-hide-zero exists");
  assert(cb && cb.type === "checkbox", "it is a checkbox");
  assert(cb && cb.checked === true, "it is checked by default");
  const label = doc.querySelector('label[for="research-hide-zero"]');
  assert(label, "a label[for=research-hide-zero] exists");
  assert(
    label && label.textContent.trim() === "Hide zero-volume rows",
    "label text is 'Hide zero-volume rows' (got: " + (label && label.textContent) + ")"
  );
  assert(
    doc.getElementById("r-dl-note").textContent === "Downloads include all rows.",
    "download note reads 'Downloads include all rows.'"
  );

  // --- switch to the 5-minute tab --------------------------------------------
  doc.getElementById("r-tab-bars5").click();
  await sleep(50);
  assert(
    doc.getElementById("r5-filter").hidden === false,
    "filter row (with the checkbox) is shown on the 5-minute tab"
  );

  const count = () => doc.getElementById("r-count").textContent;
  const pageinfo = () => doc.getElementById("r-pageinfo").textContent;
  const bodyRows = () => doc.getElementById("r-body").querySelectorAll("tr");

  // default: zero-volume bars hidden (1008 of 1169 bars have volume 0).
  // Page 1 shows the NEWEST bars, i.e. the last 11 of the 161 non-zero bars.
  assert(
    bodyRows().length === 11,
    "page 1 shows the newest 11 non-zero bars (got " + bodyRows().length + ")"
  );
  for (const tr of bodyRows()) {
    const vol = tr.querySelectorAll("td")[5].textContent;
    assert(vol !== "0", "no zero-volume row is rendered (found volume cell '" + vol + "')");
  }
  assert(
    pageinfo().indexOf("Page 1 of 4") === 0,
    "161 non-zero bars -> 4 pages (got: " + pageinfo() + ")"
  );
  assert(count().indexOf("161 of 1169") !== -1, "count reports 161 of 1169 (got: " + count() + ")");
  assert(
    count().indexOf("zero-volume rows hidden") !== -1,
    "count annotates the active filter (got: " + count() + ")"
  );

  // --- toggle off: all bars, page reset to 1 ----------------------------------
  cb.checked = false;
  cb.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  await sleep(50);
  assert(
    pageinfo().indexOf("Page 1 of 24") === 0,
    "1169 bars -> page 1 of 24 (got: " + pageinfo() + ")"
  );
  // page 1 = newest bars = last 19 of 1169
  assert(
    bodyRows().length === 19,
    "page 1 shows the newest 19 of 1169 bars (got " + bodyRows().length + ")"
  );
  // with the filter off, zero-volume bars are visible again
  let sawZero = false;
  for (const tr of bodyRows()) {
    if (tr.querySelectorAll("td")[5].textContent === "0") sawZero = true;
  }
  assert(sawZero, "zero-volume bars are rendered when the filter is off");
  assert(count().indexOf("1169 five-minute bars") !== -1, "count reports 1169 five-minute bars (got: " + count() + ")");
  assert(
    count().indexOf("zero-volume rows hidden") === -1,
    "annotation gone when filter off (got: " + count() + ")"
  );

  // --- date filter is preserved across the toggle ------------------------------
  const sel = doc.getElementById("r5-date");
  sel.value = "2026-09-24";
  sel.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  await sleep(50);
  assert(
    pageinfo().indexOf("Page 1 of 1 \u00b7 47 rows") !== -1,
    "47 bars on 2026-09-24 with filter off (got: " + pageinfo() + ")"
  );

  cb.checked = true;
  cb.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
  await sleep(50);
  assert(sel.value === "2026-09-24", "selected date is preserved across the toggle");
  assert(
    pageinfo().indexOf("Page 1 of 1 \u00b7 5 rows") !== -1,
    "5 non-zero bars on 2026-09-24 (got: " + pageinfo() + ")"
  );
  assert(
    count().indexOf("5 of 47 five-minute bars on 2026-09-24") !== -1,
    "count shows 5 of 47 for the date (got: " + count() + ")"
  );
  assert(count().indexOf("zero-volume rows hidden") !== -1, "annotation back (got: " + count() + ")");

  // --- genuine transactions are never filtered ----------------------------------
  doc.getElementById("r-tab-ticks").click();
  await sleep(50);
  assert(
    doc.getElementById("r5-filter").hidden === true,
    "filter row (with the checkbox) is hidden off the 5-minute tab"
  );
  assert(
    bodyRows().length === 9,
    "ticks tab always shows all 9 trades (got " + bodyRows().length + ")"
  );
  assert(count().indexOf("9 individual trades") !== -1, "ticks count unchanged (got: " + count() + ")");
  assert(
    count().indexOf("zero-volume rows hidden") === -1,
    "ticks count is never annotated (got: " + count() + ")"
  );

  // --- downloads remain the original, unfiltered CSVs -----------------------------
  const dl = doc.getElementById("r-download");
  assert(
    dl.href.indexOf("/static/data/01596/01596_ticks_today_20260924.csv") !== -1,
    "ticks download still points at the original CSV (got: " + dl.href + ")"
  );
  assert(
    doc.getElementById("r-dl-note").textContent === "Downloads include all rows.",
    "download note still present"
  );

  // --- persistence ------------------------------------------------------------------
  assert(
    dom.window.localStorage.getItem("hk-dashboard.research-hide-zero") === "1",
    "preference persisted as '1' (got: " +
      dom.window.localStorage.getItem("hk-dashboard.research-hide-zero") + ")"
  );
  dom.window.close();

  // stored '0' must be honoured on the next load
  const dom2 = bootDom({ "hk-dashboard.research-hide-zero": "0" });
  await sleep(50);
  assert(
    dom2.window.document.getElementById("research-hide-zero").checked === false,
    "stored '0' -> checkbox unchecked on load"
  );
  dom2.window.close();

  // corrupt/unrecognised stored values must fall back safely (default: on)
  const dom3 = bootDom({ "hk-dashboard.research-hide-zero": "not-a-bool" });
  await sleep(50);
  assert(
    dom3.window.document.getElementById("research-hide-zero").checked === true,
    "corrupt stored value -> default (checked) on load"
  );
  dom3.window.close();

  if (failures === 0) {
    console.log("OK hide-zero frontend functional test passed");
    process.exit(0);
  }
  console.error(failures + " assertion(s) failed");
  process.exit(1);
})().catch((err) => {
  console.error("ERROR: " + (err && err.stack ? err.stack : String(err)));
  process.exit(1);
});
