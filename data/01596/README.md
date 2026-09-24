# 01596.HK (翼辰实业 / YICHEN IND) data sources — Tencent/QQ

Retrieved 2026-09-24 from free public Tencent (QQ) finance endpoints.

## Files
- daily_kline_2026-09.csv — daily OHLCV, 17 rows, 2026-09-01..2026-09-23 (HKD).
  Source: https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get?_var=kline_day&param=hk01596,day,2026-08-25,2026-09-30,120,qfq
- minute_20260918.csv .. minute_20260924.csv — 1-minute bars (time/price/volume/cumulative avg),
  332 rows for full days, 232 rows for today (still trading at fetch time).
  Source: https://web.ifzq.gtimg.cn/appstock/app/day/query?code=hk01596
  (also served by web.ifzq.gtimg.cn/appstock/app/minute/query?code=hk01596 for today only)

## What Tencent does NOT provide (verified by live requests, 2026-09-24)
- NO per-trade tick (逐笔成交) data for HK stocks. The A-share tick endpoint
  https://stock.gtimg.cn/data/index.php?appn=detail&action=data&c=sh600000&p=0
  works for A-shares (70 ticks/page, p=0,1,2,...) but returns 0 bytes for every HK
  code format tested (hk01596, hk1596, 01596, ...).
- action=download&d=YYYYMMDD returns "暂无数据" (no data) for A-shares AND HK,
  today AND past dates — the variant appears deprecated/disabled.
- No date parameter is honored anywhere: minute/query and day/query ignore
  date/d/start/end params (identical responses); detail ignores date too
  (md5-identical across dates). Historical depth is fixed: 5 most recent
  trading days of 1-minute data; daily kline goes back arbitrarily (tested to
  2026-03).
- gu.qq.com HK quote page (st.gtimg.com/quotes/hk/bundle.*.js) exposes only
  minute / 5-day / kline views for HK — no 分笔/逐笔 panel. Page shows "以上港股行情延时15分钟"
  (HK quotes delayed 15 min).

## Completeness
- Ticks: NOT available for September via Tencent (any day).
- 1-minute: 2026-09-18, 21, 22, 23, 24 only (rolling 5-day window; Sep 1-17 gone).
- Daily: complete Sep 1-23; 2026-09-24 session in progress at fetch time.

## Licensing caveats
- No license/terms text on the endpoints; data is Tencent's, displayed from
  HKEX feed (15-min delay for HK). Use for personal/non-commercial research
  only; do not republish or feed paid products without permission.
- Be gentle: requests were made ~1/2s apart from a single host with browser-like
  headers.

## Pre-existing file (created 06:14 JST today, before this investigation)
- 1596.HK_2026-09_1minute.csv — 230 rows of sparse per-minute trade bars
  (datetime_hkt, open/high/low/close, volume) covering 2026-09-01..2026-09-24,
  only minutes with trading activity. Note: prices carry float32 artifacts
  (e.g. 0.23600000143051147); round to 3 decimals (HKD tick = 0.001) when
  consuming. Preserved as-is; source provenance not re-verified by this pass.
  This is the only file here with September-wide intraday coverage.

## EastMoney results (2026-09-24, second investigation pass)

Free HISTORICAL ticks for HK stocks: NOT AVAILABLE (verified by live requests).

### What works (EastMoney, free, no key)
Endpoint family: `https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=116.01596&klt=<N>&fqt=1&beg=YYYYMMDD&end=YYYYMMDD&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57`
- klt=101 daily: full history (Sep 1-24 retrieved) -> 01596_daily_ohlc.csv (18 rows)
- klt=5  5-min: full month -> 01596_5min_bars.csv (1169 rows, 18 trading days, Sep 1 09:35 .. Sep 24 14:15)
- klt=15 15-min: full month (tested)
- klt=1  1-min: TODAY ONLY — a beg=20260918&end=20260918 request silently returns today's bars instead (verified). -> 01596_1min_bars_today.csv (232 rows, Sep 24)
- NOTE: push2his intermittently resets TLS; always send `--http1.1` (curl) / force HTTP/1.1, and retry a few times.

### Ticks (逐笔成交) — today only
- `https://push2.eastmoney.com/api/qt/stock/details/get?secid=116.01596&pos=0` -> 502 for all numeric subhosts (1/5/10/17/92/70.push2) from this network; 70.push2 302-redirects to push2delay.
- WORKING (flaky, ~1 in 3-4 attempts succeeds, 502 otherwise):
  `https://push2delay.eastmoney.com/api/qt/stock/details/sse?fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f55&mpi=2000&ut=fa5fd1943c7b386f172d6893dbfba10b&fltt=2&pos=-0&secid=116.01596&wbp2u=%7C0%7C0%7C0%7Cweb`
  SSE stream, first event = full current-day tick list: "HH:MM:SS,price,lots,?,side" (side: 1=sell-initiated, 2=buy-initiated, 4=neutral; same legend as akshare stock_intraday_em).
- A `date=YYYYMMDD` param is IGNORED (md5-identical response to no-date; returns today). No historical tick endpoint found (details/get, details/sse on push2his => rc:102 data:null; push2ex getStockFenShi => rc:102 for HK and A-share alike).
- Saved: 01596_ticks_today_20260924.csv (9 ticks; 01596 is thin — only 9 trades had occurred by 14:01 HKT; SSE returns only today's session).
- This is the same endpoint akshare uses for A-share intraday ticks (akshare/stock/stock_intraday_em.py, stock_intraday_em); it serves HK secid=116.* codes too.

### Sina
- hq.sinajs.cn/list=hk01596 works (real-time quote only).
- HK trade/minute endpoints (CN_MarketDataService.getKLineData / getHKTradeData / getHKMinLine on quotes.sina.cn) => "__ERROR:3 Service not valid" for HK codes (A-share variants work; HK is disabled).

### Bottom line for Sep 2026 01596.HK
- Ticks: only 2026-09-24 partial (9 rows). Sep 1-23 ticks: not obtainable for free from EastMoney/Sina/Tencent.
- Best free intraday resolution with full-month coverage: 5-min bars (EM) + Tencent 1-min for last 5 trading days (existing files).
- For true historical ticks one would need HKEX MarketData (paid) or broker APIs (e.g. Futu/老虎, some with free tier) — out of scope here.

### Legal/reliability
- No explicit license on EM/Sina/Tencent endpoints; HK data is exchange feed (15-min delayed for HK on some feeds; EM ticks appear near-real-time from this region). Personal/research use only.
- EM push2* hosts are unofficially documented; expect breakage; always retry.
