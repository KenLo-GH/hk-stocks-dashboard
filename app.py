"""FastAPI ASGI app for Vercel deployment of the HK stocks dashboard.

Mirrors the stdlib server (hk_dashboard/server.py) exactly:

* ``GET /`` and ``GET /static/*`` -> static frontend (``static/``)
* ``GET /api/health``    -> liveness
* ``GET /api/defaults``  -> default watchlist symbols
* ``GET /api/quote``     -> ?symbol=700 (required)
* ``GET /api/history``   -> ?symbol=700&range=6M (range: 1M/3M/6M/1Y/5Y)

Errors are mapped the same way as the stdlib server:
``400`` invalid symbol/range, ``404`` unknown API endpoint or symbol not
found upstream, ``502`` upstream/transport failure — all as
``{"error": "..."}``.

The app is the module-level ``app`` object Vercel auto-detects. Upstream
fetches are fully mocked in tests (tests/test_vercel_app.py).
"""

from __future__ import annotations

import asyncio
import pathlib
from urllib.parse import unquote

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from starlette.status import HTTP_404_NOT_FOUND

from hk_dashboard import __version__, eastmoney, providers, symbols, yahoo

STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="HK Stocks Dashboard",
    version=__version__,
    description="HKEX quotes and daily history. Data: Yahoo Finance (primary) "
                "with EastMoney (read-only) final fallback - delayed, not "
                "guaranteed real-time. Not investment advice.",
)


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def _resolve_static(path: str) -> pathlib.Path | None:
    """Resolve a request path to a file inside STATIC_DIR (path-safe).

    Returns None for anything that escapes the directory or does not exist.
    ``/`` and ``/static`` map to index.html; ``/static/<file>`` maps to
    ``static/<file>`` (the frontend references assets as /static/<file>).
    """
    if path in ("/", "/static", "/static/"):
        candidate = STATIC_DIR / "index.html"
    elif path.startswith("/static/"):
        candidate = STATIC_DIR / path[len("/static/"):]
    else:
        # Also serve assets at the root level (parity with the stdlib server).
        candidate = STATIC_DIR / path.lstrip("/")
    decoded = unquote(str(candidate))
    full = pathlib.Path(decoded).resolve()
    base = STATIC_DIR.resolve()
    if full != base and base not in full.parents:
        return None
    if not full.is_file():
        return None
    return full


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/static/{path:path}", response_model=None)
def static_file(path: str):
    if not path:
        return FileResponse(STATIC_DIR / "index.html")
    full = _resolve_static("/" + path)
    if full is None:
        return _error(HTTP_404_NOT_FOUND, "Not found")
    return FileResponse(full)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "hk-stocks-dashboard", "version": __version__}


@app.get("/api/defaults")
def defaults() -> dict:
    from hk_dashboard.server import DEFAULT_SYMBOLS

    return {"symbols": DEFAULT_SYMBOLS}


@app.get("/api/quote")
async def quote(symbol: str = Query(..., description="HKEX code, e.g. 700 or 0700.HK")):
    try:
        normalized = symbols.normalize_symbol(symbol)
    except symbols.SymbolError as exc:
        return _error(400, str(exc))
    try:
        # Blocking urllib-based upstream calls; run off the event loop so
        # concurrent requests keep flowing (Vercel Fluid compute).
        return await asyncio.to_thread(
            providers.get_quote, normalized, timeout=yahoo.DEFAULT_TIMEOUT
        )
    except yahoo.InvalidRangeError as exc:
        return _error(400, str(exc))
    except (yahoo.SymbolNotFoundError, eastmoney.EastMoneySymbolNotFoundError) as exc:
        return _error(404, str(exc))
    except (yahoo.YahooHTTPError, eastmoney.EastMoneyError) as exc:
        return _error(502, str(exc))


@app.get("/api/history")
async def history(
    symbol: str = Query(..., description="HKEX code, e.g. 700 or 0700.HK"),
    range: str = Query("6M", description="1M/3M/6M/1Y/5Y"),
):
    try:
        normalized = symbols.normalize_symbol(symbol)
    except symbols.SymbolError as exc:
        return _error(400, str(exc))
    raw_range = range.strip() or "6M"
    try:
        return await asyncio.to_thread(
            providers.get_history, normalized, raw_range, timeout=yahoo.DEFAULT_TIMEOUT
        )
    except yahoo.InvalidRangeError as exc:
        return _error(400, str(exc))
    except (yahoo.SymbolNotFoundError, eastmoney.EastMoneySymbolNotFoundError) as exc:
        return _error(404, str(exc))
    except (yahoo.YahooHTTPError, eastmoney.EastMoneyError) as exc:
        return _error(502, str(exc))


@app.get("/{path:path}")
def static_root(path: str):
    """Root-level asset access (parity with the stdlib server: /app.js)."""
    full = _resolve_static("/" + path)
    if full is None:
        return _error(HTTP_404_NOT_FOUND, "Not found")
    return FileResponse(full)


# ---------------------------------------------------------------------------
# Local development (not used by Vercel; uvicorn is imported lazily so the
# Vercel runtime does not need to start a server itself).
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
