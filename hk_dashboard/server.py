"""Stdlib HTTP server: JSON API + static frontend for the dashboard."""

from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from . import eastmoney, providers, symbols, yahoo

__all__ = ["DEFAULT_SYMBOLS", "create_server", "DashboardHandler"]

#: Sensible default watchlist (major HKEX listings).
DEFAULT_SYMBOLS = [
    {"symbol": "0700.HK", "name": "Tencent Holdings"},
    {"symbol": "0005.HK", "name": "HSBC Holdings"},
    {"symbol": "0388.HK", "name": "HKEX (Stock Exchange of Hong Kong)"},
    {"symbol": "0941.HK", "name": "China Mobile"},
    {"symbol": "09988.HK", "name": "Alibaba-W"},
    {"symbol": "1810.HK", "name": "Xiaomi-W"},
    {"symbol": "02318.HK", "name": "Ping An"},
    {"symbol": "0011.HK", "name": "Hang Seng Bank"},
]


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "HKStockDashboard/" + __version__
    fetch_fn = None          # injectable Yahoo fetch (tests); default: yahoo.fetch_json
    em_fetch_fn = None       # injectable EastMoney fetch (tests); default: eastmoney.fetch_json
    upstream_timeout = yahoo.DEFAULT_TIMEOUT
    static_dir = None
    verbose_logs = True

    # ------------------------------------------------------------------ util
    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, message: str) -> None:
        self._json(code, {"error": message})

    def log_message(self, fmt, *args):  # noqa: N802 (stdlib signature)
        if self.verbose_logs:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ------------------------------------------------------------------ GET
    def do_GET(self):  # noqa: N802
        try:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path.startswith("/api/"):
                self._api(parsed)
            else:
                self._static(parsed.path)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:  # pragma: no cover - last-resort guard
            traceback.print_exc()
            try:
                self._error(500, "Internal server error")
            except Exception:
                pass

    # ------------------------------------------------------------------- API
    def _api(self, parsed) -> None:
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/api/health":
            self._json(200, {"status": "ok", "service": "hk-stocks-dashboard",
                             "version": __version__})
            return
        if path == "/api/defaults":
            self._json(200, {"symbols": DEFAULT_SYMBOLS})
            return

        if path not in ("/api/quote", "/api/history"):
            self._error(404, "Unknown API endpoint")
            return

        raw = (qs.get("symbol") or [""])[0]
        try:
            symbol = symbols.normalize_symbol(raw)
        except symbols.SymbolError as exc:
            self._error(400, str(exc))
            return

        # Yahoo Finance first; EastMoney only when both Yahoo hosts are
        # unavailable/rate-limited (see providers). Injected ``fetch_fn`` /
        # ``em_fetch_fn`` (tests) replace the two upstream legs.
        yfetch = self.fetch_fn or yahoo.fetch_json
        emfetch = self.em_fetch_fn or eastmoney.fetch_json
        try:
            if path == "/api/quote":
                data = providers.get_quote(symbol, yahoo_fetch=yfetch,
                                           em_fetch=emfetch,
                                           timeout=self.upstream_timeout)
            else:
                raw_range = (qs.get("range") or ["6M"])[0].strip() or "6M"
                data = providers.get_history(symbol, raw_range,
                                             yahoo_fetch=yfetch,
                                             em_fetch=emfetch,
                                             timeout=self.upstream_timeout)
        except yahoo.InvalidRangeError as exc:
            self._error(400, str(exc))
            return
        except (yahoo.SymbolNotFoundError, eastmoney.EastMoneySymbolNotFoundError) as exc:
            self._error(404, str(exc))
            return
        except (yahoo.YahooHTTPError, eastmoney.EastMoneyError) as exc:
            self._error(502, str(exc))
            return
        self._json(200, data)

    # ----------------------------------------------------------------- static
    def _static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        # The frontend references assets as /static/<file>; the files live
        # directly in the static directory, so strip the /static prefix.
        if path == "/static":
            path = "/index.html"
        elif path.startswith("/static/"):
            path = path[len("/static"):]
        decoded = urllib.parse.unquote(path)
        rel = posixpath.normpath(decoded).lstrip("/")
        if not rel or rel == ".." or rel.startswith("../"):
            self._error(404, "Not found")
            return
        base = os.path.realpath(
            self.static_dir
            or os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "static")
        )
        full = os.path.realpath(os.path.join(base, rel))
        try:
            common = os.path.commonpath([base, full])
        except ValueError:
            common = None
        if common != base or not os.path.isfile(full):
            self._error(404, "Not found")
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def create_server(host: str = "127.0.0.1", port: int = 8000, fetch_fn=None,
                  em_fetch_fn=None, timeout: float = yahoo.DEFAULT_TIMEOUT,
                  static_dir=None, start_thread: bool = True,
                  verbose_logs: bool = True) -> ThreadingHTTPServer:
    """Create (and optionally start) the dashboard HTTP server.

    Args:
        host/port: bind address (port 0 picks a free port, useful in tests).
        fetch_fn: optional Yahoo fetch function (injected by tests).
        em_fetch_fn: optional EastMoney fetch function (injected by tests).
        timeout: upstream HTTP timeout in seconds.
        static_dir: override the static directory.
        start_thread: run serve_forever in a daemon thread (tests). Pass False
            for foreground use and call ``serve_forever`` yourself.
    """
    handler = type("_BoundDashboardHandler", (DashboardHandler,), {
        "fetch_fn": fetch_fn,
        "em_fetch_fn": em_fetch_fn,
        "upstream_timeout": timeout,
        "static_dir": static_dir,
        "verbose_logs": verbose_logs,
    })
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    if start_thread:
        t = threading.Thread(target=httpd.serve_forever,
                             name="hk-dashboard-http", daemon=True)
        t.start()
        httpd._dashboard_thread = t  # type: ignore[attr-defined]
    return httpd
