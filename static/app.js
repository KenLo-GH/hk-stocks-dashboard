/* HK Stocks Dashboard — frontend logic (no build step, no dependencies).
 * Data: Yahoo Finance (primary) / EastMoney (fallback) via local /api/* — delayed, not guaranteed real-time. */
(function () {
  "use strict";

  var LS_FAV_KEY = "hk-dashboard.favorites.v1";
  var LS_RANGE_KEY = "hk-dashboard.last-range";
  var LS_TYPE_KEY = "hk-dashboard.last-chart-type";
  var LS_HIDE_ZERO_KEY = "hk-dashboard.research-hide-zero";

  var $ = function (id) { return document.getElementById(id); };
  var els = {
    form: $("search-form"), input: $("symbol-input"),
    quotePanel: $("quote-panel"), tablePanel: $("table-panel"),
    globalError: $("global-error"),
    watchlist: $("watchlist"), watchlistLoading: $("watchlist-loading"),
    chart: $("chart"), chartState: $("chart-state"), chartFallbackNote: $("chart-fallback-note"),
    dataBody: $("data-body"), tableState: $("table-state"),
  };

  var chart = null;
  var currentSymbol = null;
  var currentRange = "6M";
  var chartType = "candlestick";
  var loading = { quote: false, history: false };

  /* ---------------------------- utilities ---------------------------- */
  function fmtNum(v, digits) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    var d = (digits === undefined) ? 2 : digits;
    return Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function clientNormalize(raw) {
    var t = String(raw || "").trim().toUpperCase();
    if (!t) return "";
    if (t.slice(-3) === ".HK") t = t.slice(0, -3);
    if (!/^\d{1,5}$/.test(t)) return "";
    while (t.length < 4) t = "0" + t;
    return t + ".HK";
  }

  function fmtVol(v) {
    if (v === null || v === undefined) return "—";
    v = Number(v);
    if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (v >= 1e6) return (v / 1e6).toFixed(2) + "M";
    if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
    return String(Math.round(v));
  }
  function fmtSigned(v) { return v > 0 ? "+" + fmtNum(v) : fmtNum(v); }
  function setText(id, text) { $(id).textContent = text; }
  function nowTime() {
    return new Date().toLocaleString(undefined, { hour12: false });
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function api(path, params) {
    var qs = Object.keys(params).map(function (k) {
      return encodeURIComponent(k) + "=" + encodeURIComponent(params[k]);
    }).join("&");
    var url = path + (qs ? "?" + qs : "");
    return fetch(url, { headers: { "Accept": "application/json" } }).then(function (res) {
      return res.json().catch(function () { return { error: "Bad response" }; }).then(function (data) {
        if (!res.ok) throw new Error((data && data.error) || ("HTTP " + res.status));
        return data;
      });
    });
  }

  /* ---------------------------- favorites ---------------------------- */
  function loadFavorites() {
    try {
      var raw = localStorage.getItem(LS_FAV_KEY);
      if (!raw) return null;
      var arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr : null;
    } catch (e) { return null; }
  }
  function saveFavorites(list) {
    try { localStorage.setItem(LS_FAV_KEY, JSON.stringify(list)); } catch (e) { /* private mode */ }
  }
  function isFavorite(sym) {
    return loadFavorites().indexOf(sym) !== -1;
  }
  function toggleFavorite(sym) {
    var list = loadFavorites() || [];
    var i = list.indexOf(sym);
    if (i === -1) list.push(sym); else list.splice(i, 1);
    saveFavorites(list);
    renderWatchlist();
    updateStar(sym);
  }
  function updateStar(sym) {
    var star = $("fav-star");
    if (!star) return;
    star.textContent = (isFavorite(sym)) ? "★ Watching" : "☆ Watch";
  }

  function renderWatchlist() {
    var list = loadFavorites();
    els.watchlist.innerHTML = "";
    if (!list || list.length === 0) {
      var empty = document.createElement("li");
      empty.className = "state-line";
      empty.textContent = "No saved stocks yet — load a symbol and click “☆ Watch”.";
      els.watchlist.appendChild(empty);
      return;
    }
    list.forEach(function (sym) {
      var li = document.createElement("li");
      var btn = document.createElement("button");
      btn.type = "button";
      var normCur = clientNormalize(currentSymbol);
      btn.className = "chip-watch" + ((normCur && normCur === sym) ? " is-active" : "");
      btn.title = "Load " + sym;

      var nameSpan = document.createElement("span");
      nameSpan.className = "wsym";
      nameSpan.textContent = sym;

      var pxSpan = document.createElement("span");
      pxSpan.className = "wpx";
      pxSpan.dataset.sym = sym;
      pxSpan.textContent = "";

      var rm = document.createElement("button");
      rm.type = "button";
      rm.className = "wrm";
      rm.title = "Remove from watchlist";
      rm.setAttribute("aria-label", "Remove " + sym + " from watchlist");
      rm.textContent = "×";
      rm.addEventListener("click", function (ev) {
        ev.stopPropagation();
        toggleFavorite(sym);
      });

      btn.addEventListener("click", function () { loadSymbol(sym); });
      btn.appendChild(nameSpan);
      btn.appendChild(pxSpan);
      btn.appendChild(rm);
      li.appendChild(btn);
      els.watchlist.appendChild(li);
    });
    refreshWatchlistQuotes();
  }

  function refreshWatchlistQuotes() {
    var list = loadFavorites() || [];
    list.slice(0, 10).forEach(function (sym) {
      api("/api/quote", { symbol: sym }).then(function (q) {
        var px = document.querySelector('.wpx[data-sym="' + CSS.escape(sym) + '"]');
        if (!px) return;
        var cls = "";
        if (q.pctChange > 0) cls = "up";
        else if (q.pctChange < 0) cls = "down";
        px.className = "wpx " + cls;
        px.textContent = fmtNum(q.price) + (q.pctChange !== null && q.pctChange !== undefined
          ? "  " + fmtSigned(q.pctChange) + "%" : "");
      }).catch(function () { /* silent for chips */ });
    });
  }

  /* ---------------------------- quote panel ---------------------------- */
  function setPill(text, kind) {
    var pill = $("q-state");
    pill.textContent = text;
    pill.className = "pill " + (kind || "");
  }

  function renderQuote(q) {
    setText("q-name", q.name || q.symbol);
    setText("q-exchange", q.symbol + " · " + (q.exchange || "HKEX"));

    var openLabel = q.marketState === "open" ? "Market open (approx.)" : "Market closed (approx.)";
    setPill(openLabel, q.marketState === "open" ? "open" : "closed");

    setText("q-price", fmtNum(q.price));
    setText("q-cur", q.currency || "HKD");

    var ch = $("q-change");
    if (q.change !== null && q.change !== undefined && q.pctChange !== null && q.pctChange !== undefined) {
      var cls = q.change > 0 ? "up" : (q.change < 0 ? "down" : "flat");
      ch.className = "quote-change " + cls;
      ch.textContent = fmtSigned(q.change) + " (" + fmtSigned(q.pctChange) + "%)";
    } else {
      ch.className = "quote-change flat";
      ch.textContent = "—";
    }

    var upd = q.lastUpdatedIso ? "Last updated " + q.lastUpdatedIso + " · " + (q.timezone || "") : "No timestamp";
    setText("q-updated", upd + " · " + (q.disclaimer || ""));

    setText("q-open", fmtNum(q.open));
    setText("q-high", fmtNum(q.dayHigh));
    setText("q-low", fmtNum(q.dayLow));
    setText("q-prev", fmtNum(q.previousClose));
    setText("q-vol", fmtVol(q.volume));

    var star = $("q-fav");
    if (star) {
      star.textContent = isFavorite(q.symbol) ? "★ Watching" : "☆ Watch";
      star.onclick = function () { toggleFavorite(q.symbol); };
    }
    $("q-disclaimer").textContent = "Source: " + (q.source || "Yahoo Finance / EastMoney") + ". " +
      "Quotes are delayed and not guaranteed to be real-time.";
  }

  /* ---------------------------- history ---------------------------- */
  function renderTable(rows) {
    els.dataBody.innerHTML = "";
    if (!rows || rows.length === 0) {
      els.tableState.hidden = false;
      els.tableState.textContent = "No historical data returned for this range.";
      $("t-count").textContent = "0 rows";
      return;
    }
    els.tableState.hidden = true;
    $("t-count").textContent = rows.length + " trading days";
    var frag = document.createDocumentFragment();
    // show newest first
    for (var i = rows.length - 1; i >= 0; i--) {
      var r = rows[i];
      var tr = document.createElement("tr");
      var prev = i > 0 ? rows[i - 1] : null;
      var chCls = "";
      if (r.close !== null && prev && prev.close !== null) {
        chCls = r.close > prev.close ? "up" : (r.close < prev.close ? "down" : "");
      }
      var cells = [
        r.date, fmtNum(r.open), fmtNum(r.high), fmtNum(r.low),
        (r.close === null ? "—" : fmtNum(r.close)) + (chCls ? " " : ""),
        fmtVol(r.volume)
      ];
      cells.forEach(function (txt, ci) {
        var td = document.createElement("td");
        td.textContent = txt;
        if (ci === 4 && chCls) td.className = chCls;
        tr.appendChild(td);
      });
      frag.appendChild(tr);
    }
    els.dataBody.appendChild(frag);
  }

  function renderChart(rows) {
    var canvas = els.chart;
    if (!canvas) return;

    if (typeof Chart === "undefined") {
      // Fallback: simple canvas line drawing, no external libs.
      drawFallbackLine(canvas, rows);
      els.chartFallbackNote.hidden = false;
      return;
    }
    els.chartFallbackNote.hidden = true;

    if (!rows || rows.length === 0) {
      if (chart) { chart.destroy(); chart = null; }
      els.chartState.hidden = false;
      els.chartState.textContent = "No data to chart for this range.";
      return;
    }
    els.chartState.hidden = true;

    if (chartType === "candlestick") {
      if (hasChartFinancial()) {
        drawCandles(canvas, rows);
      } else {
        els.chartFallbackNote.hidden = false;
        els.chartFallbackNote.textContent =
          "Candlestick plugin unavailable — showing line chart instead.";
        drawLine(canvas, rows);
      }
      return;
    }
    drawLine(canvas, rows);
  }

  function hasChartFinancial() {
    // @sgratzl/chartjs-chart-financial registers "candlestick"/"financial" controllers.
    try {
      if (!window.Chart || !Chart.registry || !Chart.registry.getController) return false;
      return !!(Chart.registry.getController("candlestick") ||
                Chart.registry.getController("financial"));
    } catch (e) { return false; }
  }

  function chartColors() {
    return { up: "#34d399", down: "#f87171", upBg: "rgba(52,211,153,0.85)",
             downBg: "rgba(248,113,113,0.85)", grid: "rgba(143,160,184,0.14)",
             text: "#8fa0b8" };
  }

  function baseScales(c, yTitle) {
    return {
      x: {
        grid: { color: c.grid },
        ticks: { color: c.text, maxTicksLimit: 10, autoSkip: true },
        time: { displayFormats: { day: "MMM d" } }
      },
      y: {
        position: "right",
        grid: { color: c.grid },
        ticks: { color: c.text },
        title: { display: true, text: yTitle, color: c.text }
      }
    };
  }

  function drawLine(canvas, rows) {
    var c = chartColors();
    if (chart) chart.destroy();
    chart = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: rows.map(function (r) { return r.date; }),
        datasets: [
          {
            label: "Close",
            data: rows.map(function (r) { return r.close; }),
            borderColor: c.up,
            backgroundColor: "rgba(52,211,153,0.08)",
            fill: true,
            pointRadius: 0,
            borderWidth: 2,
            tension: 0.15
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { color: c.text } },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                var i = ctx.dataIndex;
                var r = rows[i];
                return "O " + fmtNum(r.open) + "  H " + fmtNum(r.high) +
                  "  L " + fmtNum(r.low) + "  C " + fmtNum(r.close) +
                  "  Vol " + fmtVol(r.volume);
              }
            }
          }
        },
        scales: baseScales(c, "Price (HKD)")
      }
    });
  }

  function drawCandles(canvas, rows) {
    var c = chartColors();
    var labels = rows.map(function (r) { return r.date; });
    var ohlc = rows.map(function (r) { return [r.open, r.high, r.low, r.close]; });
    if (chart) chart.destroy();
    chart = new Chart(canvas.getContext("2d"), {
      type: "candlestick",
      data: {
        labels: labels,
        datasets: [{
          label: "OHLC",
          data: ohlc,
          color: { up: c.upBg, down: c.downBg, unchanged: c.upBg },
          increasingColor: c.upBg,
          decreasingColor: c.downBg,
          borderColor: { up: c.up, down: c.down, unchanged: c.up },
          increasingBorder: c.up,
          decreasingBorder: c.down
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { color: c.text } },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                var r = rows[ctx.dataIndex];
                return "O " + fmtNum(r.open) + "  H " + fmtNum(r.high) +
                  "  L " + fmtNum(r.low) + "  C " + fmtNum(r.close) +
                  "  Vol " + fmtVol(r.volume);
              }
            }
          }
        },
        scales: baseScales(c, "Price (HKD)")
      }
    });
  }

  function drawFallbackLine(canvas, rows) {
    // Minimal dependency-free line chart (used only if CDN Chart.js failed).
    var ctx = canvas.getContext("2d");
    var dpr = window.devicePixelRatio || 1;
    var cssW = canvas.clientWidth || 800;
    var cssH = 300;
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
    canvas.style.height = cssH + "px";
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cssW, cssH);

    var closes = rows.map(function (r) { return r.close; }).filter(function (v) { return v !== null; });
    if (!closes.length) return;
    var min = Math.min.apply(null, closes), max = Math.max.apply(null, closes);
    if (min === max) { min -= 1; max += 1; }
    var pad = 10;
    var X = function (i) { return pad + (cssW - 2 * pad) * (i / Math.max(1, rows.length - 1)); };
    var Y = function (v) { return cssH - pad - (cssH - 2 * pad) * ((v - min) / (max - min)); };

    // grid
    ctx.strokeStyle = "rgba(143,160,184,0.14)";
    ctx.lineWidth = 1;
    for (var g = 0; g <= 4; g++) {
      var gy = pad + (cssH - 2 * pad) * (g / 4);
      ctx.beginPath(); ctx.moveTo(pad, gy); ctx.lineTo(cssW - pad, gy); ctx.stroke();
    }
    // line
    ctx.strokeStyle = "#34d399";
    ctx.lineWidth = 2;
    ctx.beginPath();
    var started = false;
    rows.forEach(function (r, i) {
      if (r.close === null) return;
      if (!started) { ctx.moveTo(X(i), Y(r.close)); started = true; }
      else ctx.lineTo(X(i), Y(r.close));
    });
    ctx.stroke();
    // axis labels
    ctx.fillStyle = "#8fa0b8";
    ctx.font = "11px monospace";
    ctx.fillText(fmtNum(max, 1), 4, pad + 8);
    ctx.fillText(fmtNum(min, 1), 4, cssH - pad - 2);
    if (rows.length) ctx.fillText(rows[0].date, pad, cssH - 4);
  }

  /* ---------------------------- data loading ---------------------------- */
  function showGlobalError(msg) {
    els.globalError.hidden = false;
    els.globalError.textContent = msg;
  }
  function clearGlobalError() { els.globalError.hidden = true; }

  function setChartLoading(on) {
    els.chartState.hidden = !on;
    els.chartState.textContent = on ? "Loading chart…" : "";
  }
  function setQuoteLoading(on) {
    if (on) els.quotePanel.hidden = true;
  }

  function loadSymbol(rawSymbol, opts) {
    opts = opts || {};
    clearGlobalError();
    currentSymbol = rawSymbol;
    var norm = clientNormalize(rawSymbol) || rawSymbol;
    updateResearchFor(norm);
    els.input.value = rawSymbol;
    els.input.setAttribute("aria-invalid", "false");

    renderWatchlist(); // refresh active highlight + quotes

    // Quote
    setQuoteLoading(true);
    els.quotePanel.dataset.has = "0";
    loading.quote = true;
    api("/api/quote", { symbol: rawSymbol })
      .then(function (q) {
        loading.quote = false;
        if (currentSymbol !== rawSymbol) return; // stale
        els.quotePanel.dataset.has = "1";
        els.quotePanel.hidden = false;
        renderQuote(q);
        var star = $("q-fav");
        if (star) {
          star.textContent = isFavorite(q.symbol) ? "★ Watching" : "☆ Watch";
          star.onclick = function () { toggleFavorite(q.symbol); };
        }
        $("footer-updated").textContent = nowTime();
      })
      .catch(function (err) {
        loading.quote = false;
        if (currentSymbol !== rawSymbol) return;
        els.quotePanel.hidden = true;
        showGlobalError("Quote failed for " + rawSymbol + ": " + err.message);
      });

    // History + chart + table
    loadHistory(rawSymbol, currentRange);
  }

  function loadHistory(sym, range) {
    currentRange = range;
    try { localStorage.setItem(LS_RANGE_KEY, range); } catch (e) {}
    document.querySelectorAll(".range-group .chip").forEach(function (b) {
      b.classList.toggle("is-active", b.dataset.range === range);
    });

    els.tablePanel.hidden = true;
    $("t-range").textContent = "";
    setChartLoading(true);
    loading.history = true;

    api("/api/history", { symbol: sym, range: range })
      .then(function (data) {
        loading.history = false;
        if (currentSymbol !== sym) return;
        var rows = data.rows || [];
        setChartLoading(false);
        $("t-range").textContent = "(" + data.range + ")";
        renderTable(rows);
        els.tablePanel.hidden = rows.length === 0;
        renderChart(rows);
      })
      .catch(function (err) {
        loading.history = false;
        if (currentSymbol !== sym) return;
        setChartLoading(false);
        showGlobalError("History failed for " + sym + " (" + range + "): " + err.message);
      });
  }

  /* ---------------------------- events ---------------------------- */
  els.form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var val = els.input.value.trim();
    if (!val) {
      els.input.setAttribute("aria-invalid", "true");
      els.input.focus();
      return;
    }
    loadSymbol(val);
  });

  document.querySelectorAll(".range-group .chip").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (currentSymbol) loadHistory(currentSymbol, btn.dataset.range);
      else showGlobalError("Load a symbol first.");
    });
  });

  document.querySelectorAll(".type-group .chip").forEach(function (btn) {
    btn.addEventListener("click", function () {
      chartType = btn.dataset.type;
      try { localStorage.setItem(LS_TYPE_KEY, chartType); } catch (e) {}
      document.querySelectorAll(".type-group .chip").forEach(function (b) {
        b.classList.toggle("is-active", b === btn);
      });
      var rows = null;
      // re-render chart with existing table rows if available
      var bodyRows = els.dataBody.querySelectorAll("tr");
      if (bodyRows.length) {
        rows = [];
        bodyRows.forEach(function (tr) {
          var tds = tr.querySelectorAll("td");
          rows.push({
            date: tds[0].textContent,
            open: parseTd(tds[1]), high: parseTd(tds[2]), low: parseTd(tds[3]),
            close: parseTd(tds[4]), volume: parseVol(tds[5])
          });
        }).reverse();
        renderChart(rows);
      } else {
        showGlobalError("Load a symbol first to change chart type.");
      }
    });
  });

  function parseTd(t) {
    if (!t) return null;
    var s = t.textContent.replace(/[+,\s]/g, "").replace(/B$/, "e9").replace(/M$/, "e6").replace(/K$/, "e3");
    if (s === "—" || s === "") return null;
    var n = parseFloat(s);
    return isNaN(n) ? null : n;
  }
  function parseVol(t) {
    var n = parseTd(t);
    return n === null ? null : Math.round(n);
  }

  /* ---------------------------- 1596.HK Sep-2026 research data ---------------------------- */
  var RESEARCH = {
    sym: "1596.HK",
    base: "/static/data/01596/",
    files: {
      daily: "01596_daily_ohlc.csv",
      bars5: "01596_5min_bars.csv",
      ticks: "01596_ticks_today_20260924.csv"
    },
    pageSize: 50,
    data: null,        // {daily:[], bars5:[], ticks:[]} once loaded
    tab: "daily",
    page: 1,
    date: "all",
    hideZero: true,
    open: false
  };
  var rEls = {};

  function cacheREls() {
    rEls.panel = $("research-panel");
    rEls.state = $("r-state");
    rEls.body = $("r-body");
    rEls.thead = $("r-thead");
    rEls.count = $("r-count");
    rEls.dl = $("r-download");
    rEls.retry = $("r-retry");
    rEls.prev = $("r-prev");
    rEls.next = $("r-next");
    rEls.pageinfo = $("r-pageinfo");
    rEls.dateFilter = $("r5-filter");
    rEls.dateSel = $("r5-date");
    rEls.hideZero = $("research-hide-zero");
  }

  function parseCsv(text) {
    // Minimal RFC-4180-ish CSV parser (handles quoted fields, CRLF, trailing
    // newlines). The research files never contain embedded newlines in quotes,
    // but the parser tolerates them anyway.
    var rows = [], cur = [], field = "", inQ = false, i, c;
    for (i = 0; i < text.length; i++) {
      c = text[i];
      if (inQ) {
        if (c === '"') {
          if (text[i + 1] === '"') { field += '"'; i++; } else { inQ = false; }
        } else field += c;
      } else if (c === '"') {
        inQ = true;
      } else if (c === ",") {
        cur.push(field); field = "";
      } else if (c === "\n" || c === "\r") {
        if (c === "\r" && text[i + 1] === "\n") i++;
        cur.push(field); field = "";
        if (cur.length > 1 || cur[0] !== "") rows.push(cur);
        cur = [];
      } else {
        field += c;
      }
    }
    cur.push(field);
    if (cur.length > 1 || cur[0] !== "") rows.push(cur);
    if (!rows.length) return [];
    var header = rows[0].map(function (h) { return h.trim(); });
    return rows.slice(1).map(function (r) {
      var o = {};
      header.forEach(function (h, i) { o[h] = (r[i] !== undefined ? r[i] : "").trim(); });
      return o;
    });
  }

  function researchLoadAll(force) {
    if (RESEARCH.data && !force) return Promise.resolve(RESEARCH.data);
    var names = ["daily", "bars5", "ticks"];
    var ps = names.map(function (k) {
      return fetch(RESEARCH.base + RESEARCH.files[k], { headers: { "Accept": "text/csv" } })
        .then(function (res) {
          if (!res.ok) throw new Error("HTTP " + res.status);
          return res.text();
        })
        .then(function (txt) { return [k, parseCsv(txt)]; });
    });
    return Promise.all(ps).then(function (pairs) {
      var out = {};
      pairs.forEach(function (pp) { out[pp[0]] = pp[1]; });
      RESEARCH.data = out;
      populateDateFilter();
      return out;
    });
  }

  function populateDateFilter() {
    var seen = {}, opts = [];
    (RESEARCH.data.bars5 || []).forEach(function (b) {
      var d = b.datetime.slice(0, 10);
      if (!seen[d]) { seen[d] = true; opts.push(d); }
    });
    opts.sort();
    var sel = rEls.dateSel;
    sel.innerHTML = "";
    var allOpt = document.createElement("option");
    allOpt.value = "all";
    allOpt.textContent = "All dates (Sep 1\u201324) \u00b7 " + (RESEARCH.data.bars5 || []).length + " bars";
    sel.appendChild(allOpt);
    opts.forEach(function (d) {
      var o = document.createElement("option");
      o.value = d;
      o.textContent = d;
      sel.appendChild(o);
    });
    sel.value = RESEARCH.date;
  }

  function loadHideZeroPref() {
    // Default: hide zero-volume rows. Stored '1'/'true' -> true,
    // '0'/'false' -> false; anything else (absent/corrupt) falls back to the
    // default so a broken localStorage can never break the UI.
    var v = true;
    try {
      var raw = localStorage.getItem(LS_HIDE_ZERO_KEY);
      if (raw === "0" || raw === "false") v = false;
      else if (raw === "1" || raw === "true") v = true;
    } catch (e) { /* private mode / storage disabled */ }
    return v;
  }
  function saveHideZeroPref(v) {
    try {
      localStorage.setItem(LS_HIDE_ZERO_KEY, v ? "1" : "0");
    } catch (e) { /* private mode / storage disabled */ }
  }

  function researchRowsFor(tab, includeZero) {
    if (!RESEARCH.data) return [];
    if (tab === "daily") return RESEARCH.data.daily;
    if (tab === "ticks") return RESEARCH.data.ticks;
    var rows = RESEARCH.data.bars5;
    if (RESEARCH.date !== "all") {
      rows = rows.filter(function (b) { return b.datetime.slice(0, 10) === RESEARCH.date; });
    }
    if (RESEARCH.hideZero && !includeZero) {
      // Only the aggregated 5-minute bars are affected: daily bars and
      // genuine individual transaction rows (ticks) are never filtered.
      rows = rows.filter(function (b) { return Number(b.volume) !== 0; });
    }
    return rows;
  }

  function researchHeaders(tab) {
    if (tab === "ticks") return ["Time (HKT)", "Price (HKD)", "Volume", "Side"];
    return ["Date", "Open", "High", "Low", "Close", "Volume", "Turnover (HKD)"];
  }

  function researchCountText(tab, rows, total) {
    if (tab === "daily") return rows.length + " trading days (Sep 1\u201324)";
    if (tab === "ticks") return rows.length + " individual trades \u2014 2026-09-24 only (10:22:18\u201314:00:25 HKT)";
    var shown = rows.length === total ? total : rows.length + " of " + total;
    var note = RESEARCH.hideZero ? " \u00b7 zero-volume rows hidden" : "";
    if (RESEARCH.date === "all") return shown + " five-minute bars \u00b7 18 trading days (Sep 1\u201324)" + note;
    return shown + " five-minute bars on " + RESEARCH.date + note;
  }

  function researchDownloadFor(tab) {
    var map = {
      daily: ["01596_daily_ohlc.csv", "Download daily OHLC CSV"],
      bars5: ["01596_5min_bars.csv", "Download 5-minute bars CSV"],
      ticks: ["01596_ticks_today_20260924.csv", "Download Sep 24 individual trades CSV"]
    };
    var m = map[tab];
    rEls.dl.href = RESEARCH.base + m[0];
    rEls.dl.setAttribute("download", m[0]);
    rEls.dl.textContent = m[1];
    rEls.dl.setAttribute("aria-label", m[1]);
  }

  function setResearchState(msg, kind) {
    rEls.state.hidden = !msg;
    rEls.state.textContent = msg || "";
    rEls.state.className = "state-line" + (kind ? " " + kind : "");
  }

  function renderResearchTable() {
    var tab = RESEARCH.tab;
    var rows = researchRowsFor(tab);
    // The total ignores the zero-volume filter so the count can show
    // how many of the selected date's bars are visible ("N of M").
    var total = RESEARCH.data ? researchRowsFor(tab, true).length : 0;
    var pages = Math.max(1, Math.ceil(rows.length / RESEARCH.pageSize));
    if (RESEARCH.page > pages) RESEARCH.page = pages;
    if (RESEARCH.page < 1) RESEARCH.page = 1;

    // header
    var heads = researchHeaders(tab);
    rEls.thead.innerHTML = "";
    var tr = document.createElement("tr");
    heads.forEach(function (h) {
      var th = document.createElement("th");
      th.textContent = h;
      th.setAttribute("scope", "col");
      tr.appendChild(th);
    });
    rEls.thead.appendChild(tr);

    // rows (newest first)
    rEls.body.innerHTML = "";
    var start = (pages - RESEARCH.page) * RESEARCH.pageSize;
    var slice = rows.slice(start, start + RESEARCH.pageSize).reverse();
    var frag = document.createDocumentFragment();
    slice.forEach(function (r) {
      var trr = document.createElement("tr");
      var cells;
      if (tab === "ticks") {
        var sideTxt = r.side === "1" ? "1 \u00b7 sell-initiated"
          : (r.side === "2" ? "2 \u00b7 buy-initiated"
          : (r.side === "4" ? "4 \u00b7 neutral" : "\u2014"));
        cells = [r.time, fmtNum(r.price, 3), fmtVol(r.volume), sideTxt];
      } else {
        cells = [r.datetime, fmtNum(r.open, 3), fmtNum(r.high, 3), fmtNum(r.low, 3),
                 fmtNum(r.close, 3), fmtVol(r.volume), fmtNum(r.turnover_hkd, 0)];
      }
      cells.forEach(function (txt, ci) {
        var td = document.createElement("td");
        td.textContent = txt;
        if (tab === "ticks" && ci === 3) td.className = "r-side";
        trr.appendChild(td);
      });
      frag.appendChild(trr);
    });
    rEls.body.appendChild(frag);

    // count + pager + state
    rEls.count.textContent = researchCountText(tab, rows, total);
    rEls.pageinfo.textContent = "Page " + RESEARCH.page + " of " + pages + (rows.length ? " \u00b7 " + rows.length + " rows" : "");
    rEls.prev.disabled = RESEARCH.page <= 1;
    rEls.next.disabled = RESEARCH.page >= pages;
    if (!rows.length) {
      setResearchState(tab === "bars5" && RESEARCH.date !== "all"
        ? "No five-minute bars recorded for " + RESEARCH.date + "."
        : "No data available for this tab.");
    } else {
      setResearchState("");
    }
  }

  function researchLoading() {
    setResearchState("Loading 1596.HK September 2026 research data\u2026");
    rEls.body.innerHTML = "";
    rEls.count.textContent = "";
    rEls.retry.hidden = false;
    rEls.retry.disabled = true;
  }

  function researchError(msg) {
    setResearchState("Failed to load research data: " + msg, "error");
    rEls.count.textContent = "";
    rEls.retry.hidden = false;
    rEls.retry.disabled = false;
  }

  function switchResearchTab(tab, fromJump) {
    RESEARCH.tab = tab;
    RESEARCH.page = 1;
    document.querySelectorAll(".research-tab").forEach(function (b) {
      var on = b.dataset.rtab === tab;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
    });
    if (rEls.panel) {
      var active = document.querySelector(".research-tab.is-active");
      if (active) rEls.panel.setAttribute("aria-labelledby", active.id);
    }
    rEls.dateFilter.hidden = tab !== "bars5";
    researchDownloadFor(tab);
    if (RESEARCH.data) renderResearchTable();
  }

  function showResearchPanel() {
    RESEARCH.open = true;
    if (!rEls.panel) cacheREls();
    rEls.panel.hidden = false;
    if (!RESEARCH.data) {
      researchLoading();
      researchLoadAll().then(function () {
        if (!RESEARCH.open) return;
        rEls.retry.hidden = true;
        renderResearchTable();
      }).catch(function (err) {
        if (!RESEARCH.open) return;
        researchError(err && err.message ? err.message : "unknown error");
      });
    }
  }

  function hideResearchPanel() {
    RESEARCH.open = false;
    if (rEls.panel) rEls.panel.hidden = true;
  }

  function updateResearchFor(norm) {
    if (!rEls.panel) cacheREls();
    if (norm === RESEARCH.sym) {
      showResearchPanel();
    } else if (RESEARCH.open) {
      hideResearchPanel();
    }
  }

  function initResearch() {
    cacheREls();
    var order = ["daily", "bars5", "ticks"];
    document.querySelectorAll(".research-tab").forEach(function (btn) {
      btn.addEventListener("click", function () { switchResearchTab(btn.dataset.rtab); });
      btn.addEventListener("keydown", function (ev) {
        var idx = order.indexOf(RESEARCH.tab);
        var next = null;
        if (ev.key === "ArrowRight") next = order[(idx + 1) % order.length];
        else if (ev.key === "ArrowLeft") next = order[(idx - 1 + order.length) % order.length];
        else if (ev.key === "Home") next = order[0];
        else if (ev.key === "End") next = order[order.length - 1];
        if (next) {
          ev.preventDefault();
          switchResearchTab(next, true);
          var b = document.querySelector('.research-tab[data-rtab="' + next + '"]');
          if (b) b.focus();
        }
      });
    });
    rEls.dateSel.addEventListener("change", function () {
      RESEARCH.date = rEls.dateSel.value;
      RESEARCH.page = 1;
      if (RESEARCH.data) renderResearchTable();
    });
    if (rEls.hideZero) {
      rEls.hideZero.checked = RESEARCH.hideZero = loadHideZeroPref();
      rEls.hideZero.addEventListener("change", function () {
        RESEARCH.hideZero = rEls.hideZero.checked;
        saveHideZeroPref(RESEARCH.hideZero);
        RESEARCH.page = 1; // the selected date is preserved
        if (RESEARCH.data) renderResearchTable();
      });
    }
    rEls.prev.addEventListener("click", function () {
      if (RESEARCH.page > 1) { RESEARCH.page--; renderResearchTable(); }
    });
    rEls.next.addEventListener("click", function () {
      var pages = Math.max(1, Math.ceil(researchRowsFor(RESEARCH.tab).length / RESEARCH.pageSize));
      if (RESEARCH.page < pages) { RESEARCH.page++; renderResearchTable(); }
    });
    rEls.retry.addEventListener("click", function () {
      if (!RESEARCH.data) {
        researchLoading();
        researchLoadAll(true).then(function () {
          rEls.retry.hidden = true;
          renderResearchTable();
        }).catch(function (err) {
          researchError(err && err.message ? err.message : "unknown error");
        });
      } else {
        renderResearchTable();
      }
    });
    $("research-close").addEventListener("click", hideResearchPanel);
    var jump = $("research-jump");
    if (jump) {
      jump.addEventListener("click", function (ev) {
        ev.preventDefault();
        showResearchPanel();
        rEls.panel.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
    switchResearchTab("daily");
  }

  /* ---------------------------- init ---------------------------- */
  function restorePrefs() {
    try {
      var r = localStorage.getItem(LS_RANGE_KEY);
      if (r) {
        currentRange = r;
        document.querySelectorAll(".range-group .chip").forEach(function (b) {
          b.classList.toggle("is-active", b.dataset.range === r);
        });
      }
      var t = localStorage.getItem(LS_TYPE_KEY);
      if (t) {
        chartType = t;
        document.querySelectorAll(".type-group .chip").forEach(function (b) {
          b.classList.toggle("is-active", b.dataset.type === t);
        });
      }
    } catch (e) {}
  }

  function init() {
    initResearch();
    restorePrefs();
    els.watchlistLoading.hidden = false;
    api("/api/defaults", {})
      .then(function (d) {
        var stored = loadFavorites();
        if (!stored) {
          // first visit: seed with sensible HK defaults (user can remove)
          saveFavorites((d.symbols || []).map(function (s) { return s.symbol; }));
        }
      })
      .catch(function () {})
      .then(function () {
        els.watchlistLoading.hidden = true;
        renderWatchlist();
        // auto-load first favorite or 0700
        var favs = loadFavorites();
        loadSymbol(favs && favs.length ? favs[0] : "0700");
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
