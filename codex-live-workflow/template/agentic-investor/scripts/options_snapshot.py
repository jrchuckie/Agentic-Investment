from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_investor_common import ROOT, now_iso, read_json, today_stamp, write_json


OPTIONS_DIR = ROOT / "data" / "options"
LATEST_PATH = OPTIONS_DIR / "options_latest.json"

DEFAULT_SYMBOLS = [
    "QQQ",
    "SPY",
    "SMH",
    "SOXX",
    "NVDA",
    "AMD",
    "MU",
    "SNDK",
    "WDC",
    "STX",
    "PLTR",
    "AVGO",
    "AMZN",
    "GOOG",
    "INTC",
    "ALAB",
    "MRVL",
    "LITE",
    "CRWV",
    "BE",
    "TSLA",
]


def _to_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def _request_json(url: str, timeout: int = 12) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _expiration_dte(expiration_epoch: int) -> int:
    expiry = datetime.fromtimestamp(expiration_epoch, timezone.utc)
    now = datetime.now(timezone.utc)
    return max(0, (expiry.date() - now.date()).days)


def _pick_expirations(expirations: list[int], max_count: int = 4) -> list[int]:
    candidates = [epoch for epoch in expirations if 14 <= _expiration_dte(epoch) <= 70]
    if not candidates:
        candidates = list(expirations[:max_count])
    candidates.sort(key=lambda epoch: abs(_expiration_dte(epoch) - 35))
    return candidates[:max_count]


def _normalize_contract(row: dict[str, Any]) -> dict[str, Any]:
    bid = _to_float(row.get("bid"))
    ask = _to_float(row.get("ask"))
    last = _to_float(row.get("lastPrice"))
    mid = (bid + ask) / 2 if bid and ask and bid > 0 and ask > 0 else last
    spread_pct = ((ask - bid) / mid * 100) if bid and ask and mid and ask >= bid else None
    return {
        "contractSymbol": row.get("contractSymbol"),
        "strike": _to_float(row.get("strike")),
        "currency": row.get("currency"),
        "lastPrice": last,
        "bid": bid,
        "ask": ask,
        "mid": round(mid, 4) if mid is not None else None,
        "spreadPct": round(spread_pct, 2) if spread_pct is not None else None,
        "volume": _to_int(row.get("volume")),
        "openInterest": _to_int(row.get("openInterest")),
        "impliedVolatility": _to_float(row.get("impliedVolatility")),
        "inTheMoney": bool(row.get("inTheMoney")),
        "lastTradeDate": row.get("lastTradeDate"),
    }


def _fetch_symbol(symbol: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(symbol.upper())
    base_url = f"https://query2.finance.yahoo.com/v7/finance/options/{encoded}"
    try:
        first = _request_json(base_url)
    except Exception as rest_exc:
        try:
            return _fetch_symbol_yfinance(symbol)
        except Exception as yf_exc:  # noqa: BLE001 - preserve both provider errors.
            raise RuntimeError(f"Yahoo REST failed: {rest_exc}; yfinance fallback failed: {yf_exc}") from yf_exc

    result = ((first.get("optionChain") or {}).get("result") or [{}])[0]
    quote = result.get("quote") or {}
    expirations = [int(epoch) for epoch in result.get("expirationDates", []) if epoch]
    picked = _pick_expirations(expirations)
    chains: dict[str, Any] = {}

    for expiration in picked:
        url = f"{base_url}?date={expiration}"
        data = _request_json(url)
        option_result = ((data.get("optionChain") or {}).get("result") or [{}])[0]
        options = (option_result.get("options") or [{}])[0]
        expiry_key = datetime.fromtimestamp(expiration, timezone.utc).date().isoformat()
        chains[expiry_key] = {
            "expirationEpoch": expiration,
            "dte": _expiration_dte(expiration),
            "calls": [_normalize_contract(row) for row in options.get("calls", [])],
            "puts": [_normalize_contract(row) for row in options.get("puts", [])],
        }

    return {
        "symbol": symbol.upper(),
        "quote": {
            "regularMarketPrice": _to_float(quote.get("regularMarketPrice")),
            "regularMarketChangePercent": _to_float(quote.get("regularMarketChangePercent")),
            "regularMarketVolume": _to_int(quote.get("regularMarketVolume")),
            "source": "Yahoo Finance optionChain REST",
        },
        "expirations": [
            {
                "expiration": datetime.fromtimestamp(epoch, timezone.utc).date().isoformat(),
                "expirationEpoch": epoch,
                "dte": _expiration_dte(epoch),
            }
            for epoch in expirations
        ],
        "chains": chains,
        "dataQuality": "PASS" if chains else "MISSING",
    }


def _row_from_yfinance_record(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    last_trade = normalized.get("lastTradeDate")
    if hasattr(last_trade, "isoformat"):
        normalized["lastTradeDate"] = last_trade.isoformat()
    elif last_trade is not None:
        normalized["lastTradeDate"] = str(last_trade)
    return _normalize_contract(normalized)


def _fetch_symbol_yfinance(symbol: str) -> dict[str, Any]:
    import yfinance as yf  # type: ignore

    cache_dir = ROOT / ".cache" / "yfinance"
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        yf.set_tz_cache_location(str(cache_dir))
        import yfinance.cache as yf_cache  # type: ignore

        yf_cache.set_cache_location(str(cache_dir))
    except Exception:
        pass

    ticker = yf.Ticker(symbol.upper())
    options = list(ticker.options or [])
    picked_dates = []
    for date_text in options:
        try:
            expiry = datetime.fromisoformat(str(date_text)).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        dte = max(0, (expiry.date() - datetime.now(timezone.utc).date()).days)
        if 14 <= dte <= 70:
            picked_dates.append((abs(dte - 35), date_text, dte))
    if not picked_dates:
        for date_text in options[:4]:
            try:
                expiry = datetime.fromisoformat(str(date_text)).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            dte = max(0, (expiry.date() - datetime.now(timezone.utc).date()).days)
            picked_dates.append((abs(dte - 35), date_text, dte))

    picked_dates.sort(key=lambda item: item[0])
    chains: dict[str, Any] = {}
    for _, date_text, dte in picked_dates[:4]:
        chain = ticker.option_chain(date_text)
        calls = chain.calls.to_dict("records") if getattr(chain, "calls", None) is not None else []
        puts = chain.puts.to_dict("records") if getattr(chain, "puts", None) is not None else []
        chains[str(date_text)] = {
            "expirationEpoch": None,
            "dte": dte,
            "calls": [_row_from_yfinance_record(row) for row in calls],
            "puts": [_row_from_yfinance_record(row) for row in puts],
        }

    quote = {}
    try:
        info = ticker.fast_info
        quote = {
            "regularMarketPrice": _to_float(getattr(info, "last_price", None) or info.get("last_price")),
            "regularMarketChangePercent": None,
            "regularMarketVolume": _to_int(getattr(info, "last_volume", None) or info.get("last_volume")),
            "source": "yfinance option_chain fallback",
        }
    except Exception:
        quote = {"source": "yfinance option_chain fallback"}

    return {
        "symbol": symbol.upper(),
        "quote": quote,
        "expirations": [{"expiration": str(date), "expirationEpoch": None, "dte": dte} for _, date, dte in picked_dates],
        "chains": chains,
        "dataQuality": "PASS" if chains else "MISSING",
    }


def _symbols_from_market() -> list[str]:
    market = read_json(ROOT / "data" / "market" / "latest.json", {})
    rows = market.get("watchSymbols", []) or []
    active = []
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if symbol and symbol in DEFAULT_SYMBOLS:
            active.append(symbol)
    merged = []
    for symbol in [*active, *DEFAULT_SYMBOLS]:
        if symbol not in merged:
            merged.append(symbol)
    return merged[:24]


def build_options_snapshot(symbols: list[str] | None = None) -> dict[str, Any]:
    symbols = symbols or _symbols_from_market()
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}

    for symbol in symbols:
        try:
            row = _fetch_symbol(symbol)
            if row.get("dataQuality") == "PASS":
                results[symbol] = row
            else:
                errors[symbol] = "No option chain returned."
        except Exception as exc:  # noqa: BLE001 - keep the dashboard resilient.
            errors[symbol] = f"{type(exc).__name__}: {exc}"

    status = "PASS" if results else "WARN"
    return {
        "task": "options_snapshot",
        "timestamp": now_iso(),
        "status": status,
        "source": "Yahoo Finance optionChain REST",
        "symbolsRequested": symbols,
        "symbolsWithChains": len(results),
        "symbols": results,
        "errors": errors,
        "policy": {
            "advisoryOnly": True,
            "brokerExecutionEnabled": False,
            "note": "Option chains are used only for paper-trade idea generation and require manual approval.",
        },
    }


def write_outputs(result: dict[str, Any]) -> tuple[Path, Path]:
    OPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = OPTIONS_DIR / f"{today_stamp()}_options_snapshot.json"
    write_json(snapshot, result)
    write_json(LATEST_PATH, result)
    report = OPTIONS_DIR / f"{today_stamp()}_options_snapshot.md"
    errors = result.get("errors", {})
    lines = [
        "# Options Snapshot",
        "",
        f"- Status: {result.get('status')}",
        f"- Timestamp: {result.get('timestamp')}",
        f"- Symbols with chains: {result.get('symbolsWithChains')}/{len(result.get('symbolsRequested', []))}",
        "",
        "## Errors",
        "",
    ]
    lines += [f"- {symbol}: {error}" for symbol, error in sorted(errors.items())] or ["- None"]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report, snapshot


def main() -> int:
    result = build_options_snapshot()
    report, latest = write_outputs(result)
    print(json.dumps({
        "task": "options_snapshot",
        "status": result.get("status"),
        "symbolsWithChains": result.get("symbolsWithChains"),
        "report": str(report),
        "latest": str(latest),
    }, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"PASS", "WARN"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
