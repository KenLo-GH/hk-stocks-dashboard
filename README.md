# HK Stocks Dashboard

A dependency-free (Python 3 standard library only) single-page web dashboard
for **Hong Kong Stock Exchange (HKEX)** equities.

- Enter any HKEX code — `700`, `0700`, or `0700.HK` — and the backend
  normalizes it to the canonical 4/5-digit Yahoo Finance symbol
  (`0700.HK`) without altering already-valid forms.
- Shows the latest (delayed) price, absolute and % change, approximate
  market state, OHLC/volume cards, an interactive candlestick or
  line+volume history chart (ranges **1M / 3M / 6M / 1Y / 5Y**), and a
  historical daily OHLCV table.
- Watchlist/favorites persisted in the browser via `localStorage`,
  seeded with sensible HK defaults (Tencent 0700, HSBC 0005, HKEX 0388,
  China Mobile 0941, Alibaba-W 9988, Xiaomi-W 1810, Ping An 02318,
  Hang Seng Bank 0011).
- No API keys, no server-side state, no pip installs. The only browser
  libraries are Chart.js (plus the financial plugin) loaded from the
  jsDelivr CDN; if the CDN is unreachable the app degrades to a built-in
  canvas line chart.

## Data source and limitations

- Data comes from **Yahoo Finance's unauthenticated `v8/finance/chart`
  endpoint**, fetched **server-side** by this app with a fixed User-Agent
  and a 10 s timeout. The primary host is
  `https://query1.finance.yahoo.com`; if it fails with a retryable error
  (HTTP 429, HTTP 5xx, or a transport/network error), the identical request
  is retried **at most once** against the fallback host
  `https://query2.finance.yahoo.com`.
- **Final fallback: EastMoney (read-only).** If *both* Yahoo hosts fail
  with retryable errors (rate-limiting, 5xx, or transport failure), the
  same quote/history request is served from EastMoney's unauthenticated
  APIs, with the symbol normalized to its 5-digit HKEX code
  (`0700.HK` -> `116.00700`):
  - quote: `https://push2.eastmoney.com/api/qt/stock/get` (fields
    `f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f170,f86`, fixed `ut` token);
    the server may 302-redirect to `https://push2delay.eastmoney.com`
    (delayed feed), which is likewise allowlisted. Prices are scaled by
    `f59` (decimal precision, divided by `10**f59`); `f170/100` is the %
    change; `f47` volume; `f86` last-trade epoch.
  - daily history: `https://push2his.eastmoney.com/api/qt/stock/kline/get`
    (`klt=101`, `fqt=1`, `fields1=f1..f13`, `fields2=f51..f61`,
    `rtntype=6`, `end=20500101`, `beg` derived from the selected range);
    kline rows are `date,open,close,high,low,volume,...`.
  Deterministic upstream answers (HTTP 404 unknown symbol, non-retryable
  HTTP errors, malformed responses) **never** trigger the EastMoney
  fallback — they are surfaced as-is.
- **Every response names its actual source** (`source` field and the UI
  "Source:" line): "Yahoo Finance ..." or "EastMoney ...".
- Quotes are **delayed and not guaranteed to be real-time** (both sources).
  Historical daily bars may be subject to revision. **Not investment advice.**
- The "market open/closed" badge is an approximation based on exchange
  hours (Mon–Fri, 09:30–12:00 and 13:00–16:00 HKT); **HK public holidays
  are not accounted for**, so it may be wrong on holidays.
- Only five upstream hosts are ever contacted —
  `https://query1.finance.yahoo.com`, `https://query2.finance.yahoo.com`
  (Yahoo) and `https://push2.eastmoney.com`,
  `https://push2delay.eastmoney.com`, `https://push2his.eastmoney.com`
  (EastMoney) — each under a strict HTTPS allowlist; lookalike hosts such
  as `https://query1.finance.yahoo.com.evil.com` or
  `https://push2.eastmoney.com.evil.com` are rejected, as are non-HTTPS
  schemes, userinfo tricks, and redirects to non-allowlisted hosts.
  Upstream URLs are built from hardcoded paths plus a
  server-validated/normalized symbol and a whitelisted range key — user
  input cannot steer requests to arbitrary upstream hosts.
- Yahoo (and EastMoney) may rate-limit or change their unauthenticated
  endpoints without notice; on failure the UI shows an error state and the
  API returns meaningful HTTP codes (400 bad input, 404 unknown symbol,
  502 upstream failure). EastMoney's hosts can be intermittently flaky
  from some networks; the EastMoney leg performs at most one short-delay
  retry of a retryable failure, and `live_verify.py` (real network)
  re-checks end-to-end availability.

## 1596.HK — September 2026 research dataset

A dedicated, clearly-labeled **research data** section is built into the
frontend for the recovered 1596.HK (YICHEN IND) September 2026 dataset.
The validated CSVs are shipped as static files under
`static/data/01596/` (byte-identical copies of the source files in
`data/01596/`):

| File | Contents |
| --- | --- |
| `01596_daily_ohlc.csv` | daily OHLCV, 18 rows, 2026-09-01..24 (EastMoney, qfq) |
| `01596_5min_bars.csv` | 5-minute bars, 1169 rows, 18 trading days (EastMoney, qfq) |
| `01596_1min_bars_today.csv` | 1-minute bars, 232 rows, 2026-09-24 only |
| `01596_ticks_today_20260924.csv` | individual trades, 9 rows, 2026-09-24 up to 14:01 HKT (side: 1 = sell-initiated, 2 = buy-initiated, 4 = neutral) |
| `sources.txt` | sources, retrieval details, and full limitations |

UI behavior:

* The section appears automatically when **1596.HK** is loaded (any
  input form: `1596`, `0156`, `1596.HK`) and is also reachable from the
  permanent jump link in the lookup hint for every other symbol. It can
  be closed with the ✕ button and re-opened any time.
* Tabs (A11y tablist, arrow-key navigable): **Daily**, **5-minute**
  (with a per-date filter), and **Sep 24 individual trades**.
* Paginated table (50 rows/page, newest first), record-count line, and
  loading / error (with retry) / empty states.
* Per-tab **Download CSV** button plus direct download links for all
  four files and `sources.txt`.
* Explicit, honest labels: the Daily/5-minute/1-minute tables are
  **aggregated bars, not individual transactions**; **only the Sep 24
  file contains genuine individual trades**, and transaction ticks for
  Sep 1-23 were **unavailable from free sources** (Tencent, EastMoney,
  Sina — verified 2026-09-24).
* Tests: `tests/test_research_data.py` (static file integrity vs.
  `data/01596/`, routes, UI text, row counts).

## Run

    cd /root/hk-stocks-dashboard
    python3 -m hk_dashboard            # default: http://0.0.0.0:8000
    # options:
    python3 -m hk_dashboard --host 127.0.0.1 --port 8080 --timeout 8

Then open <http://localhost:8000> (or `http://<host>:8080`). Requires
Python >= 3.9 and an outbound HTTPS connection to Yahoo Finance.

### API

| Endpoint | Params | Notes |
|---|---|---|
| `GET /api/health` | — | liveness |
| `GET /api/defaults` | — | default watchlist symbols |
| `GET /api/quote?symbol=700` | `symbol` required | normalized quote plus change plus market state |
| `GET /api/history?symbol=0700&range=6M` | `range` in `1M,3M,6M,1Y,5Y` (default `6M`) | daily OHLCV rows |

Errors: `400` invalid symbol/range, `404` symbol not found upstream,
`502` upstream/transport failure — all as `{"error": "..."}`.

## Deploy to Vercel (FastAPI / Python)

The repo ships a Vercel-ready FastAPI app (root `app.py`, ASGI `app`)
that exposes the **identical** routes as the stdlib server
(`/`, `/static/*`, `/api/health`, `/api/defaults`, `/api/quote`,
`/api/history`) with the same error mapping (`400` bad symbol/range,
`404` unknown endpoint or symbol, `502` upstream failure) and the same
data sources (Yahoo Finance primary, EastMoney read-only fallback).
The stdlib server (`python3 -m hk_dashboard`) is preserved for local
use and both coexist.

### GitHub -> Vercel (no CLI)

1. Push this repository to GitHub.
2. Go to <https://vercel.com/new>, pick the repo, and deploy. Vercel
   auto-detects the Python framework: it reads `pyproject.toml`
   (dependencies: `fastapi`, `uvicorn`; Python 3.12) and finds the
   FastAPI `app` instance in `app.py` (also pinned by
   `[tool.vercel] entrypoint = "app:app"`). `vercel.json` caps the
   function `maxDuration` at 30 s (upstream fetches timeout at 10 s,
   so this is ample headroom).
3. Done — the deployed URL serves the dashboard UI and the JSON API.

### Vercel CLI

    vercel link     # or: vercel link --project <name>
    vercel          # preview
    vercel prod     # production

### Run locally against the FastAPI app

    pip install fastapi uvicorn
    python3 app.py                       # http://127.0.0.1:8000
    # or: uvicorn app:app --host 127.0.0.1 --port 8000

Notes:

* Upstream fetches are blocking (stdlib `urllib`) and run in a thread
  per request, so concurrency is fine under Vercel's Fluid compute.
* Serverless cold starts pay the module-import cost once per spin-up;
  no other per-request state is needed.
* `live_verify.py` (real network) re-checks end-to-end availability.

## Vercel / FastAPI tests

The FastAPI app is covered by `tests/test_vercel_app.py` using
FastAPI's `TestClient` with **fully mocked upstreams** (canned Yahoo
and EastMoney payloads from `tests/fakes.py`) — no network required:

    pip install fastapi httpx pytest
    python3 -m pytest tests/test_vercel_app.py -v

Coverage mirrors `tests/test_api.py`: root/static serving (including
`/static/*` and root-level assets), `/api/health`, `/api/defaults`,
`/api/quote` (Yahoo primary, EastMoney fallback on 429, 400/404/502
error mapping, missing `symbol` -> 422), and `/api/history` (all
ranges, default range, 400/404/502).

## Tests

Automated unit tests (Python stdlib `unittest`, upstream fully mocked —
no network required) cover symbol normalization, range validation,
quote/history parsing with canned payloads, market-state logic, upstream
guardrails (host allowlist, error mapping), and the HTTP API end-to-end
via an in-process server:

    cd /root/hk-stocks-dashboard
    python3 -m unittest discover -s tests -v

## Layout

    hk_dashboard/
      symbols.py     # HKEX code normalization (700 / 0700 / 0700.HK -> 0700.HK)
      yahoo.py       # Yahoo upstream client: URL building, allowlist, parsing
      eastmoney.py   # EastMoney upstream client (read-only final fallback)
      providers.py   # orchestration: Yahoo first, EastMoney final fallback
      server.py      # stdlib HTTP server: /api/* plus static file serving (path-safe)
      __main__.py    # `python3 -m hk_dashboard`
    live_verify.py   # bounded real-network check (quote + 1M history)
    static/          # single-page UI (index.html, style.css, app.js)
    tests/           # unittest suite with mocked upstream data
    app.py           # FastAPI ASGI app (Vercel entrypoint, same routes/API)
    pyproject.toml   # Python 3.12 + fastapi + uvicorn (+ [tool.vercel])
    vercel.json      # function maxDuration for the Vercel deployment
    .gitignore
