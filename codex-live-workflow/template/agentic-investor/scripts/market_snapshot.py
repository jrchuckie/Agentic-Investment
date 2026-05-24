from __future__ import annotations

import csv
import io
import json
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agentic_investor_common import REPORTS_DIR, ROOT, now_iso, today_stamp, write_json
from openbb_data import (
    diagnostics as openbb_diagnostics,
    fetch_equity_history as fetch_openbb_equity_history,
    fetch_equity_quotes as fetch_openbb_equity_quotes,
    fetch_true_macro_series as fetch_openbb_true_macro_series,
)


DATA_DIR = ROOT / "data" / "market"
LATEST_PATH = DATA_DIR / "latest.json"


INDEX_SYMBOLS = [
    ("SPY", "S&P 500"),
    ("QQQ", "Nasdaq 100"),
    ("DIA", "Dow"),
    ("IWM", "Russell 2000"),
]

FUTURES_SYMBOLS = [
    ("ES=F", "S&P 500 Futures"),
    ("NQ=F", "Nasdaq 100 Futures"),
    ("YM=F", "Dow Futures"),
    ("RTY=F", "Russell 2000 Futures"),
]

PROXY_SYMBOLS = [
    ("VIXY", "VIX proxy"),
    ("TLT", "US 20Y Treasury proxy"),
    ("UUP", "US Dollar proxy"),
]

SECTOR_ETFS = [
    ("XLK", "Technology"),
    ("XLC", "Communication"),
    ("XLY", "Consumer Disc."),
    ("XLF", "Financials"),
    ("XLV", "Healthcare"),
    ("XLI", "Industrials"),
    ("XLE", "Energy"),
    ("XLU", "Utilities"),
    ("XLP", "Consumer Staples"),
    ("XLB", "Materials"),
    ("XLRE", "Real Estate"),
    ("SMH", "Semiconductors"),
]

WATCH_SYMBOLS = [
    "QQQ",
    "SPY",
    "SMH",
    "SOXX",
    "DRAM",
    "SNXX",
    "MULL",
    "EWY",
    "005930.KS",
    "000660.KS",
    "285A.T",
    "2408.TW",
    "2344.TW",
    "NVDA",
    "AMD",
    "PLTR",
    "GOOG",
    "GOOGL",
    "AMZN",
    "MSFT",
    "META",
    "AAPL",
    "TSLA",
    "AVGO",
    "TSM",
    "ASML",
    "ARM",
    "MRVL",
    "MU",
    "WDC",
    "STX",
    "PSTG",
    "NTAP",
    "SNDK",
    "LRCX",
    "MPWR",
    "NXPI",
    "ON",
    "ALAB",
    "TSEM",
    "INTC",
    "CRWD",
    "PANW",
    "ORCL",
    "GTLB",
    "SNOW",
    "DDOG",
    "APP",
    "RDDT",
    "CRCL",
    "COIN",
    "HOOD",
    "PINS",
    "TXN",
    "WBD",
    "HHH",
    "FUN",
    "BP",
    "LW",
    "SDRL",
    "NFLX",
    "SHOP",
    "SPOT",
    "ROKU",
    "BILL",
    "TEM",
    "SMCI",
    "DELL",
    "ANET",
    "VRT",
    "COHR",
    "LITE",
    "POET",
    "GEV",
    "BE",
    "CRWV",
    "CORZ",
    "CRDO",
    "NVT",
    "FN",
    "CLS",
    "CEG",
    "VST",
    "NRG",
    "TLN",
    "KGS",
    "FLNC",
    "DLR",
    "EQIX",
    "CIEN",
    "MOD",
    "JCI",
    "CARR",
    "HUBB",
    "GNRC",
    "CIFR",
    "CLSK",
    "RIOT",
    "HUT",
    "BTDR",
    "BITF",
    "PWR",
    "ETN",
    "EQT",
    "LBRT",
    "PUMP",
    "PSIX",
    "NBIS",
    "IREN",
    "APLD",
    "GME",
    "EBAY",
]

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=6mo&interval=1d"
YAHOO_INTRADAY_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1m&includePrePost=true"
STOOQ_DAILY_CSV_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"


def _round(value: Any, digits: int = 2) -> float | None:
    try:
        if value is None:
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _series_from_history(history: dict[str, list[dict[str, Any]]], symbol: str, limit: int = 40) -> list[float]:
    bars = history.get(symbol, [])
    closes = [_round(row.get("close"), 4) for row in bars[-limit:]]
    return [value for value in closes if value is not None]


def _record_map(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row.get("symbol"): row for row in records if row.get("symbol")}


def _market_item(symbol: str, label: str, records: dict[str, dict[str, Any]], history: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    row = records.get(symbol, {})
    return {
        "symbol": symbol,
        "label": label,
        "last": _round(row.get("last_price")),
        "dayChangePct": _round(row.get("day_change_pct")),
        "momentum30dPct": _round(row.get("momentum_30d_pct")),
        "aboveMa50": row.get("above_ma50"),
        "sparkline": _series_from_history(history, symbol),
        "source": row.get("source") or "moomoo OpenD read-only quotes/history",
        "bid": _round(row.get("bid")),
        "ask": _round(row.get("ask")),
        "volume": _round(row.get("volume"), 0),
        "asOf": row.get("as_of"),
        "dataQuality": "PASS" if row else "MISSING",
    }


def _fetch_text(url: str, timeout: int = 12, accept: str = "text/csv,*/*") -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "agentic-investor/1.0 read-only macro feed",
            "Accept": accept,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _macro_item(
    symbol: str,
    label: str,
    rows: list[tuple[str, float]],
    unit: str,
    source: str,
    url: str,
) -> dict[str, Any]:
    rows = [(day, value) for day, value in rows if value is not None]
    rows.sort(key=lambda item: item[0])
    values = [value for _, value in rows]
    last = values[-1] if values else None
    prev = values[-2] if len(values) >= 2 else None
    base_30d = values[-31] if len(values) >= 31 else values[0] if values else None
    day_change_pct = ((last - prev) / abs(prev) * 100) if last is not None and prev else None
    momentum_30d_pct = ((last - base_30d) / abs(base_30d) * 100) if last is not None and base_30d else None
    return {
        "symbol": symbol,
        "label": label,
        "last": _round(last, 4),
        "dayChangePct": _round(day_change_pct),
        "momentum30dPct": _round(momentum_30d_pct),
        "aboveMa50": None,
        "sparkline": [_round(value, 4) for value in values[-40:] if value is not None],
        "unit": unit,
        "asOf": rows[-1][0] if rows else None,
        "source": source,
        "sourceUrl": url,
        "dataQuality": "PASS" if rows else "MISSING",
    }


def _parse_fred_series(series_id: str, symbol: str, label: str, unit: str) -> dict[str, Any]:
    url = FRED_CSV_URL.format(series_id=series_id)
    text = _fetch_text(url)
    reader = csv.DictReader(io.StringIO(text))
    rows: list[tuple[str, float]] = []
    for row in reader:
        day = row.get("observation_date") or row.get("DATE") or row.get("date")
        raw_value = row.get(series_id)
        if not day or raw_value in {None, "", "."}:
            continue
        try:
            rows.append((day, float(raw_value)))
        except ValueError:
            continue
    item = _macro_item(symbol, label, rows, unit, f"FRED:{series_id}", url)
    item["provider"] = "FRED"
    return item


def _parse_yahoo_chart(
    symbol: str,
    item_symbol: str,
    label: str,
    unit: str,
    value_scale: float = 1.0,
) -> dict[str, Any]:
    url = YAHOO_CHART_URL.format(symbol=symbol)
    text = _fetch_text(url, accept="application/json,*/*")
    payload = json.loads(text)
    result = payload.get("chart", {}).get("result", [{}])[0]
    timestamps = result.get("timestamp", [])
    closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
    rows: list[tuple[str, float]] = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
        rows.append((day, float(close) * value_scale))
    item = _macro_item(item_symbol, label, rows, unit, f"Yahoo chart:{symbol}", url)
    item["provider"] = "Yahoo Finance"
    return item


def _fetch_yahoo_equity_history(symbols: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, str]]:
    records: dict[str, dict[str, Any]] = {}
    history: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}

    for symbol in symbols:
        try:
            url = YAHOO_CHART_URL.format(symbol=symbol)
            text = _fetch_text(url, accept="application/json,*/*")
            payload = json.loads(text)
            result = payload.get("chart", {}).get("result", [{}])[0]
            timestamps = result.get("timestamp", [])
            quote = result.get("indicators", {}).get("quote", [{}])[0]
            closes = quote.get("close", [])
            volumes = quote.get("volume", [])
            rows: list[dict[str, Any]] = []
            for ts, close, volume in zip(timestamps, closes, volumes):
                if close is None:
                    continue
                day = datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
                rows.append({"date": day, "close": float(close), "volume": float(volume or 0), "source": "Yahoo chart"})
            if not rows:
                errors[symbol] = "Yahoo chart returned no close rows."
                continue

            closes_only = [row["close"] for row in rows]
            last = closes_only[-1]
            prev = closes_only[-2] if len(closes_only) >= 2 else None
            base_30d = closes_only[-31] if len(closes_only) >= 31 else closes_only[0]
            ma50_window = closes_only[-50:] if len(closes_only) >= 50 else closes_only
            ma50 = sum(ma50_window) / len(ma50_window) if ma50_window else None
            day_change_pct = ((last - prev) / abs(prev) * 100) if prev else None
            momentum_30d_pct = ((last - base_30d) / abs(base_30d) * 100) if base_30d else None
            avg_volume = sum(row["volume"] for row in rows[-20:]) / min(20, len(rows)) if rows else None

            history[symbol] = rows
            records[symbol] = {
                "symbol": symbol,
                "last_price": last,
                "day_change_pct": day_change_pct,
                "momentum_30d_pct": momentum_30d_pct,
                "above_ma50": bool(ma50 is not None and last > ma50),
                "avg_volume_20d": avg_volume,
                "avg_dollar_volume_20d": (avg_volume * last) if avg_volume is not None else None,
                "source": "Yahoo chart read-only fallback",
                "as_of": rows[-1].get("date"),
            }
        except Exception as exc:
            errors[symbol] = str(exc)

    return records, history, errors


def _fetch_yahoo_intraday_quotes(symbols: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    records: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for symbol in symbols:
        try:
            url = YAHOO_INTRADAY_CHART_URL.format(symbol=symbol)
            text = _fetch_text(url, accept="application/json,*/*")
            payload = json.loads(text)
            result = payload.get("chart", {}).get("result", [{}])[0]
            meta = result.get("meta", {})
            timestamps = result.get("timestamp", [])
            closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
            rows = [(int(ts), float(close)) for ts, close in zip(timestamps, closes) if close is not None]
            if not rows:
                errors[symbol] = "Yahoo intraday chart returned no close rows."
                continue
            latest_ts, latest_price = rows[-1]
            previous = meta.get("chartPreviousClose") or meta.get("previousClose")
            day_change_pct = ((latest_price - float(previous)) / abs(float(previous)) * 100) if previous else None
            as_of = datetime.fromtimestamp(latest_ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
            records[symbol] = {
                "symbol": symbol,
                "last_price": latest_price,
                "day_change_pct": day_change_pct,
                "source": "Yahoo chart intraday overlay",
                "source_url": url,
                "as_of": as_of,
            }
        except Exception as exc:
            errors[symbol] = str(exc)
    return records, errors


def _fetch_moomoo_research_snapshot(symbols: list[str]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    try:
        from moomoo_data import fetch_research_snapshot
    except BaseException as exc:
        return [], {"moomoo_import": str(exc)}
    try:
        research = fetch_research_snapshot(symbols, lookback_days=420)
        return research.get("records", []), research.get("errors", {})
    except Exception as exc:
        return [], {"research_snapshot": str(exc)}


def _fetch_moomoo_daily_history(symbols: list[str], start: str, end: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    try:
        from moomoo_data import fetch_daily_history_resilient
    except BaseException as exc:
        return {}, {"moomoo_import": str(exc)}
    try:
        return fetch_daily_history_resilient(symbols, start=start, end=end)
    except Exception as exc:
        return {}, {"daily_history": str(exc)}


def _parse_stooq_daily(symbol: str, item_symbol: str, label: str, unit: str) -> dict[str, Any]:
    url = STOOQ_DAILY_CSV_URL.format(symbol=symbol)
    text = _fetch_text(url)
    reader = csv.DictReader(io.StringIO(text))
    rows: list[tuple[str, float]] = []
    for row in reader:
        day = row.get("Date")
        close = row.get("Close")
        if not day or close in {None, "", "No data"}:
            continue
        try:
            rows.append((day, float(close)))
        except ValueError:
            continue
    item = _macro_item(item_symbol, label, rows, unit, f"Stooq:{symbol}", url)
    item["provider"] = "Stooq"
    return item


def _fetch_usdcnh() -> dict[str, Any]:
    yahoo_errors: list[str] = []
    for yahoo_symbol in ("USDCNH=X", "CNH=X"):
        try:
            return _parse_yahoo_chart(yahoo_symbol, "USDCNH", "USD/CNH", "CNH per USD")
        except Exception as exc:
            yahoo_errors.append(f"{yahoo_symbol}: {exc}")

    for stooq_symbol in ("usdcnh", "usdcnh.fx"):
        try:
            item = _parse_stooq_daily(stooq_symbol, "USDCNH", "USD/CNH", "CNH per USD")
            item["fallbackErrors"] = yahoo_errors
            return item
        except Exception as exc:
            yahoo_errors.append(f"{stooq_symbol}: {exc}")
    raise RuntimeError("; ".join(yahoo_errors))


def _fetch_vix() -> dict[str, Any]:
    errors: list[str] = []
    try:
        return _parse_fred_series("VIXCLS", "VIX", "VIX spot", "index points")
    except Exception as exc:
        errors.append(f"FRED VIXCLS: {exc}")
    try:
        item = _parse_yahoo_chart("^VIX", "VIX", "VIX spot", "index points")
        item["fallbackErrors"] = errors
        return item
    except Exception as exc:
        errors.append(f"Yahoo ^VIX: {exc}")
    raise RuntimeError("; ".join(errors))


def _fetch_dgs10() -> dict[str, Any]:
    errors: list[str] = []
    try:
        return _parse_fred_series("DGS10", "DGS10", "10Y Treasury Yield", "percent")
    except Exception as exc:
        errors.append(f"FRED DGS10: {exc}")
    try:
        item = _parse_yahoo_chart("^TNX", "DGS10", "10Y Treasury Yield", "percent")
        item["fallbackErrors"] = errors
        item["source"] = "Yahoo chart:^TNX"
        return item
    except Exception as exc:
        errors.append(f"Yahoo ^TNX: {exc}")
    raise RuntimeError("; ".join(errors))


def _fetch_true_macro_series() -> tuple[list[dict[str, Any]], dict[str, str]]:
    fetchers = {
        "VIX": _fetch_vix,
        "DGS10": _fetch_dgs10,
        "USDCNH": _fetch_usdcnh,
    }
    items: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for key, fetcher in fetchers.items():
        try:
            item = fetcher()
            if item.get("dataQuality") == "PASS":
                items.append(item)
            else:
                errors[key] = "No usable observations returned."
        except Exception as exc:
            errors[key] = str(exc)
    return items, errors


def build_market_snapshot() -> dict[str, Any]:
    symbols = sorted(
        {symbol for symbol, _ in INDEX_SYMBOLS + FUTURES_SYMBOLS + PROXY_SYMBOLS + SECTOR_ETFS} | set(WATCH_SYMBOLS)
    )
    start = (date.today() - timedelta(days=110)).isoformat()
    end = date.today().isoformat()
    errors: dict[str, str] = {}
    records: dict[str, dict[str, Any]] = {}
    history: dict[str, list[dict[str, Any]]] = {}
    data_source_diagnostics: dict[str, Any] = {"priority": ["OpenBB", "Yahoo_public_fallback", "public_macro_feeds", "moomoo_OpenD_fallback"]}
    openbb_diag = openbb_diagnostics()
    data_source_diagnostics["openbb"] = openbb_diag

    if openbb_diag.get("available"):
        openbb_records, openbb_history, openbb_errors = fetch_openbb_equity_history(symbols, start, end)
        records.update(openbb_records)
        history.update(openbb_history)
        data_source_diagnostics["openbb_equity"] = {
            "requested": len(symbols),
            "records": len(openbb_records),
            "errors": openbb_errors,
        }
    else:
        data_source_diagnostics["openbb_equity"] = {
            "requested": len(symbols),
            "records": 0,
            "errors": {"openbb_import": openbb_diag.get("error")},
        }

    missing_quote_symbols = [symbol for symbol in symbols if symbol not in records]
    missing_history_symbols = [symbol for symbol in symbols if symbol not in history]

    if missing_quote_symbols or missing_history_symbols:
        yahoo_symbols = sorted(set(missing_quote_symbols + missing_history_symbols))
        yahoo_records, yahoo_history, yahoo_errors = _fetch_yahoo_equity_history(yahoo_symbols)
        records.update({symbol: row for symbol, row in yahoo_records.items() if symbol not in records})
        history.update({symbol: rows for symbol, rows in yahoo_history.items() if symbol not in history})
        errors.update({f"yahoo.{key}": value for key, value in yahoo_errors.items()})
        data_source_diagnostics["yahoo_equity"] = {
            "requested": len(yahoo_symbols),
            "records": len(yahoo_records),
            "history": len(yahoo_history),
            "errors": yahoo_errors,
        }

    missing_quote_symbols = [symbol for symbol in symbols if symbol not in records]
    missing_history_symbols = [symbol for symbol in symbols if symbol not in history]

    if missing_quote_symbols:
        moomoo_records, moomoo_errors = _fetch_moomoo_research_snapshot(missing_quote_symbols)
        records.update(_record_map(moomoo_records))
        errors.update(moomoo_errors)
        data_source_diagnostics["moomoo_quotes"] = {
            "requested": len(missing_quote_symbols),
            "records": len(moomoo_records),
            "errors": moomoo_errors,
        }

    if missing_history_symbols:
        fallback_history, history_errors = _fetch_moomoo_daily_history(missing_history_symbols, start=start, end=end)
        history.update(fallback_history)
        errors.update(history_errors)
        data_source_diagnostics["moomoo_history"] = {
            "requested": len(missing_history_symbols),
            "records": len(fallback_history),
            "errors": history_errors,
        }

    intraday_records: dict[str, dict[str, Any]] = {}
    intraday_errors: dict[str, str] = {}
    if openbb_diag.get("available"):
        intraday_records, intraday_errors = fetch_openbb_equity_quotes(symbols)
        data_source_diagnostics["openbb_intraday_quote"] = {
            "requested": len(symbols),
            "records": len(intraday_records),
            "errors": intraday_errors,
            "note": "OpenBB yfinance quote fetcher updates last/day-change/bid/ask when available; daily OpenBB history still drives 30D momentum and sparklines.",
        }

    missing_intraday_symbols = [symbol for symbol in symbols if symbol not in intraday_records]
    yahoo_intraday_records: dict[str, dict[str, Any]] = {}
    yahoo_intraday_errors: dict[str, str] = {}
    if missing_intraday_symbols:
        yahoo_intraday_records, yahoo_intraday_errors = _fetch_yahoo_intraday_quotes(missing_intraday_symbols)
        intraday_records.update(yahoo_intraday_records)

    for symbol, quote in intraday_records.items():
        if symbol not in records:
            continue
        records[symbol] = {
            **records[symbol],
            "last_price": quote.get("last_price", records[symbol].get("last_price")),
            "day_change_pct": quote.get("day_change_pct", records[symbol].get("day_change_pct")),
            "source": quote.get("source") or records[symbol].get("source"),
            "source_url": quote.get("source_url"),
            "as_of": quote.get("as_of") or records[symbol].get("as_of"),
            "bid": quote.get("bid", records[symbol].get("bid")),
            "ask": quote.get("ask", records[symbol].get("ask")),
            "volume": quote.get("volume", records[symbol].get("volume")),
        }
    data_source_diagnostics["yahoo_intraday_quote"] = {
        "requested": len(missing_intraday_symbols),
        "records": len(yahoo_intraday_records),
        "errors": yahoo_intraday_errors,
        "note": "Fallback overlay updates last/day-change with 1m Yahoo chart data if OpenBB quote misses a symbol.",
    }

    true_macro: list[dict[str, Any]] = []
    macro_errors: dict[str, str] = {}
    if openbb_diag.get("available"):
        openbb_macro, openbb_macro_errors = fetch_openbb_true_macro_series(start, end)
        true_macro.extend(openbb_macro)
        data_source_diagnostics["openbb_macro"] = {
            "records": len(openbb_macro),
            "errors": openbb_macro_errors,
        }
    true_macro_by_symbol = {row.get("symbol"): row for row in true_macro}
    if {"VIX", "DGS10", "USDCNH"} - set(true_macro_by_symbol):
        public_macro, public_macro_errors = _fetch_true_macro_series()
        for item in public_macro:
            if item.get("symbol") not in true_macro_by_symbol:
                true_macro.append(item)
                true_macro_by_symbol[item.get("symbol")] = item
        macro_errors.update(public_macro_errors)
        data_source_diagnostics["public_macro"] = {
            "records": len(public_macro),
            "errors": public_macro_errors,
        }

    status = "PASS" if records and not errors else "WARN" if records else "FAIL"
    indices = [_market_item(symbol, label, records, history) for symbol, label in INDEX_SYMBOLS]
    futures = [_market_item(symbol, label, records, history) for symbol, label in FUTURES_SYMBOLS]
    proxies = [_market_item(symbol, label, records, history) for symbol, label in PROXY_SYMBOLS]
    sectors = [_market_item(symbol, label, records, history) for symbol, label in SECTOR_ETFS]
    watch = [_market_item(symbol, symbol, records, history) for symbol in WATCH_SYMBOLS]
    missing_true_series = []
    for name, symbol, proxy, next_step in [
        ("VIX spot", "VIX", "VIXY", "接入 CBOE/FRED/OpenBB 后替换 proxy。"),
        ("10Y Treasury yield", "DGS10", "TLT", "接入 US Treasury/FRED/OpenBB 后显示真实 yield 和 yield sparkline。"),
        ("USD/CNH", "USDCNH", "UUP", "接入外汇数据源后显示真实汇率、日内变化和换汇提示。"),
    ]:
        if symbol not in true_macro_by_symbol:
            missing_true_series.append(
                {
                    "name": name,
                    "status": "MISSING",
                    "currentProxy": proxy,
                    "nextStep": next_step,
                }
            )
            if symbol in macro_errors:
                errors[f"true_macro.{symbol}"] = macro_errors[symbol]

    return {
        "task": "market_snapshot",
        "timestamp": now_iso(),
        "status": status,
        "source": "OpenBB-first; Yahoo/public macro fallback; moomoo OpenD read-only fallback",
        "lookbackStart": start,
        "lookbackEnd": end,
        "indices": indices,
        "futures": futures,
        "trueMacroSeries": true_macro,
        "macroProxies": proxies,
        "sectorEtfs": sectors,
        "watchSymbols": watch,
        "missingTrueSeries": missing_true_series,
        "errors": errors,
        "dataSourceDiagnostics": data_source_diagnostics,
        "assumptions": {
            "execution": "read-only market data; no broker order and no account query",
            "proxy_warning": "ETF proxies remain as fallbacks when public true macro feeds are unavailable.",
        },
    }


def write_outputs(result: dict[str, Any]) -> tuple[Path, Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = DATA_DIR / f"{today_stamp()}_market_snapshot.json"
    write_json(snapshot_path, result)
    if result.get("status") != "FAIL" or not LATEST_PATH.exists():
        write_json(LATEST_PATH, result)

    report = REPORTS_DIR / f"{today_stamp()}_market_snapshot.md"
    lines = [
        "# Market Snapshot",
        "",
        f"- Timestamp: {result.get('timestamp')}",
        f"- Status: {result.get('status')}",
        f"- Source: {result.get('source')}",
        "",
        "## Indices",
        "",
        "| Symbol | Label | Last | Day % | 30D % | Above MA50 |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in result.get("indices", []):
        lines.append(
            f"| {row.get('symbol')} | {row.get('label')} | {row.get('last')} | {row.get('dayChangePct')} | {row.get('momentum30dPct')} | {row.get('aboveMa50')} |"
        )

    lines += [
        "",
        "## Index Futures (Yahoo symbols)",
        "",
        "| Symbol | Label | Last | Day % | 30D % | Above MA50 |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in result.get("futures", []):
        lines.append(
            f"| {row.get('symbol')} | {row.get('label')} | {row.get('last')} | {row.get('dayChangePct')} | {row.get('momentum30dPct')} | {row.get('aboveMa50')} |"
        )
    lines += [
        "",
        "## True Macro Series",
        "",
        "| Symbol | Label | Last | Unit | As Of | Day % | Source |",
        "|---|---|---:|---|---|---:|---|",
    ]
    for row in result.get("trueMacroSeries", []):
        lines.append(
            f"| {row.get('symbol')} | {row.get('label')} | {row.get('last')} | {row.get('unit')} | "
            f"{row.get('asOf')} | {row.get('dayChangePct')} | {row.get('source')} |"
        )
    lines += [
        "",
        "## Data Gaps",
        "",
    ]
    if result.get("missingTrueSeries"):
        for row in result.get("missingTrueSeries", []):
            lines.append(f"- {row.get('name')}: {row.get('status')}，当前 proxy: {row.get('currentProxy')}")
    else:
        lines.append("- No true macro series gap in this run.")
    if result.get("errors"):
        lines += ["", "## Errors", ""]
        for key, value in result.get("errors", {}).items():
            lines.append(f"- {key}: {value}")
    report.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report, LATEST_PATH


def main() -> int:
    result = build_market_snapshot()
    report, latest = write_outputs(result)
    print(json.dumps({"task": "market_snapshot", "status": result.get("status"), "report": str(report), "latest": str(latest)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
