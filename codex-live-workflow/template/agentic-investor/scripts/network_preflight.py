from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from agentic_investor_common import ROOT, now_iso, write_json


OUTPUT_PATH = ROOT / "data" / "health" / "network_preflight_latest.json"

TEST_URLS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=1d&interval=1m",
    "https://fred.stlouisfed.org/",
    "https://oauth2.googleapis.com/token",
]


def _test_url(url: str, timeout: int = 8) -> dict[str, Any]:
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "agentic-investor-network-preflight/1.0"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {
                "url": url,
                "ok": True,
                "status": getattr(response, "status", None),
                "errorType": None,
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        return {
            "url": url,
            "ok": True,
            "status": exc.code,
            "errorType": "HTTPError",
            "error": str(exc),
            "note": "HTTP reached the remote service; network egress is available.",
        }
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        return {
            "url": url,
            "ok": False,
            "status": None,
            "errorType": type(exc).__name__,
            "error": str(exc),
        }


def _dns_check(host: str) -> dict[str, Any]:
    try:
        addrs = sorted({item[4][0] for item in socket.getaddrinfo(host, 443)})
        return {"host": host, "ok": True, "addresses": addrs[:8], "error": None}
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        return {"host": host, "ok": False, "addresses": [], "error": str(exc)}


def build_preflight() -> dict[str, Any]:
    checks = [_test_url(url) for url in TEST_URLS]
    dns = [_dns_check("query1.finance.yahoo.com"), _dns_check("fred.stlouisfed.org")]
    ok_count = sum(1 for item in checks if item["ok"])
    yahoo_ready = any("query1.finance.yahoo.com" in item["url"] and item["ok"] for item in checks)
    fred_ready = any("fred.stlouisfed.org" in item["url"] and item["ok"] for item in checks)
    firebase_ready = any("oauth2.googleapis.com" in item["url"] and item["ok"] for item in checks)
    if yahoo_ready and fred_ready and firebase_ready:
        status = "PASS"
    elif yahoo_ready:
        status = "WARN"
    else:
        status = "FAIL"
    return {
        "timestamp": now_iso(),
        "status": status,
        "okCount": ok_count,
        "totalCount": len(checks),
        "marketDataReady": yahoo_ready,
        "macroDataReady": fred_ready,
        "firebaseReady": firebase_ready,
        "checks": checks,
        "dns": dns,
        "interpretation": (
            "Market data and publishing network checks passed."
            if status == "PASS"
            else "Yahoo market data is reachable; continue market refresh but mark macro/Firebase gaps if present."
            if status == "WARN"
            else "Yahoo market data is blocked or unavailable in this process. Preserve last good market data."
        ),
    }


def main() -> int:
    result = build_preflight()
    write_json(OUTPUT_PATH, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"PASS", "WARN"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
