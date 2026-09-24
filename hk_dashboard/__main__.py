"""Entry point: ``python3 -m hk_dashboard``."""

from __future__ import annotations

import argparse

from . import yahoo
from .server import create_server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="hk-dashboard",
        description="Hong Kong (HKEX) stock dashboard. "
                    "Data: Yahoo Finance first, EastMoney fallback (unauthenticated) "
                    "- delayed, not guaranteed real-time.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="port (default: 8000)")
    parser.add_argument("--timeout", type=float, default=yahoo.DEFAULT_TIMEOUT,
                        help="upstream HTTP timeout in seconds (default: 10)")
    args = parser.parse_args(argv)

    httpd = create_server(host=args.host, port=args.port, timeout=args.timeout,
                          start_thread=False)
    print("HK Stock Dashboard running at http://%s:%d" % (args.host, httpd.server_address[1]))
    print("Data source: Yahoo Finance (unauthenticated), with EastMoney "
        "(unauthenticated, read-only) as final fallback - delayed, not "
        "guaranteed real-time.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
