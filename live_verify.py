"""Live verification (real network, no mocks): quote + 1M history for 0700.HK
through the actual Yahoo-first/EastMoney-fallback client.

Bounded: at most 5 attempts per endpoint with a short pause (upstream
endpoints can be intermittently rate-limited or flaky). Writes a report to
live_verify_report.txt. Exits 0 when both endpoints returned data.
"""
import json
import time

from hk_dashboard import providers

REPORT = "live_verify_report.txt"
MAX_ATTEMPTS = 5
PAUSE = 3.0


def try_endpoint(fetch, max_attempts=MAX_ATTEMPTS):
    last_err = None
    for i in range(1, max_attempts + 1):
        try:
            data = fetch()
            print("attempt %d: OK (source: %s)" % (i, data.get("source")))
            return data, None
        except Exception as exc:  # noqa: BLE001 - report any upstream failure
            last_err = "%s: %s" % (type(exc).__name__, exc)
            print("attempt %d: FAILED - %s" % (i, last_err))
            time.sleep(PAUSE)
    return None, last_err


def main():
    lines = []
    quote, quote_err = try_endpoint(
        lambda: providers.get_quote("0700.HK", timeout=10.0))
    if quote is None:
        lines.append("QUOTE: FAILED after %d attempts - %s"
                     % (MAX_ATTEMPTS, quote_err))
    else:
        lines.append("QUOTE: OK (source: %s)" % quote["source"])
        lines.append("LIVE 0700.HK quote:")
        for k in ("symbol", "name", "price", "previousClose", "change",
                  "pctChange", "open", "dayHigh", "dayLow", "volume",
                  "currency", "marketState", "lastUpdatedIso", "source",
                  "disclaimer"):
            lines.append("  %-15s %s" % (k + ":", quote.get(k)))

    hist, hist_err = try_endpoint(
        lambda: providers.get_history("0700.HK", "1M", timeout=10.0))
    if hist is None:
        lines.append("HISTORY: FAILED after %d attempts - %s"
                     % (MAX_ATTEMPTS, hist_err))
    else:
        lines.append("HISTORY: OK (source: %s), %d rows"
                     % (hist["source"], len(hist["rows"])))
        lines.append("last 3 bars:")
        for r in hist["rows"][-3:]:
            lines.append("  %s" % json.dumps(r))

    ok = quote is not None and hist is not None
    lines.append("")
    lines.append("RESULT: %s" % ("SUCCESS" if ok else "PARTIAL/FAILED"))
    with open(REPORT, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
