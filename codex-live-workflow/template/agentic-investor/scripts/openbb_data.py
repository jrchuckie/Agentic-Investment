from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from agentic_investor_common import ROOT


OPENBB_CACHE_DIR = ROOT / "vendor" / "runtime-cache" / "openbb"
YFINANCE_CACHE_DIR = ROOT / "vendor" / "runtime-cache" / "yfinance-openbb"


def _openbb_source(openbb_module: Any) -> dict[str, Any]:
    paths = getattr(openbb_module, "__path__", []) or []
    return {
        "file": str(getattr(openbb_module, "__file__", "") or ""),
        "paths": [str(path) for path in paths],
    }


def _openbb_source_text(source: dict[str, Any] | None) -> str:
    if not source:
        return "unknown location"
    locations = [source.get("file") or "", *(source.get("paths") or [])]
    locations = [location for location in locations if location]
    return ", ".join(locations) if locations else "unknown location"


def _round(value: Any, digits: int = 4) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        return round(number, digits)
    except (TypeError, ValueError):
        return None


def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    for method_name in ("model_dump", "dict"):
        method = getattr(row, method_name, None)
        if callable(method):
            return method()
    data = {}
    for key in ("date", "symbol", "open", "high", "low", "close", "volume", "year_10"):
        if hasattr(row, key):
            data[key] = getattr(row, key)
    return data


def _response_rows(response: Any) -> list[dict[str, Any]]:
    to_df = getattr(response, "to_df", None)
    if callable(to_df):
        try:
            df = to_df()
            if hasattr(df, "reset_index"):
                return df.reset_index().to_dict(orient="records")
        except Exception:
            pass

    if isinstance(response, dict):
        results = response.get("results", [])
    else:
        results = getattr(response, "results", [])
    return [_row_dict(row) for row in results or []]


def _date_text(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "date") and callable(value.date):
        try:
            return value.date().isoformat()
        except Exception:
            pass
    return str(value)[:10]


def _market_metrics(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    clean: list[dict[str, Any]] = []
    for row in rows:
        close = _round(row.get("close") or row.get("Close"))
        if close is None:
            continue
        clean.append(
            {
                "date": _date_text(row.get("date") or row.get("Date") or row.get("index")),
                "close": close,
                "volume": _round(row.get("volume") or row.get("Volume"), 2),
            }
        )
    clean.sort(key=lambda item: item["date"])
    if not clean:
        return None

    closes = [row["close"] for row in clean]
    last = closes[-1]
    previous = closes[-2] if len(closes) > 1 else None
    base_30d = closes[-31] if len(closes) >= 31 else closes[0]
    ma50 = sum(closes[-50:]) / min(50, len(closes))
    day_change = ((last - previous) / previous * 100) if previous else None
    momentum_30d = ((last - base_30d) / base_30d * 100) if base_30d else None
    return {
        "last_price": _round(last, 4),
        "day_change_pct": _round(day_change, 4),
        "momentum_30d_pct": _round(momentum_30d, 4),
        "above_ma50": bool(last > ma50) if ma50 else None,
        "sparkline": [_round(value, 4) for value in closes[-40:]],
        "as_of": clean[-1]["date"],
        "rows": clean,
    }


def _load_obb() -> Any:
    try:
        _configure_provider_caches()
        from openbb import obb  # type: ignore

        _patch_openbb_return_annotations()
        return obb
    except ModuleNotFoundError:
        raise
    except Exception as exc:
        source: dict[str, Any] | None = None
        try:
            import openbb  # type: ignore

            source = _openbb_source(openbb)
        except Exception:
            pass
        raise ImportError(
            "OpenBB was imported but the `obb` entrypoint is unavailable. "
            f"source={_openbb_source_text(source)}; original={exc}"
        ) from exc


def _configure_provider_caches() -> None:
    OPENBB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    YFINANCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        import yfinance.cache as yfinance_cache  # type: ignore

        yfinance_cache.set_cache_location(str(YFINANCE_CACHE_DIR))
    except Exception:
        pass


def _patch_openbb_return_annotations() -> None:
    """Expose generated OBBject_* classes expected by OpenBB static routers."""
    try:
        import openbb_core.app.provider_interface as provider_module  # type: ignore
        from openbb_core.app.provider_interface import ProviderInterface  # type: ignore
    except Exception:
        return

    try:
        provider_interface = ProviderInterface()
        for name, annotation in provider_interface.return_annotations.items():
            setattr(provider_module, f"OBBject_{name}", annotation)
    except Exception:
        return


def is_openbb_available() -> bool:
    try:
        _load_obb()
        return True
    except Exception:
        return False


def fetch_equity_history(
    symbols: list[str],
    start_date: str,
    end_date: str,
    provider: str = "yfinance",
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, str]]:
    try:
        obb = _load_obb()
    except Exception as exc:
        return {}, {}, {"openbb_import": str(exc)}

    records: dict[str, dict[str, Any]] = {}
    history: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    try:
        endpoint = obb.equity.price.historical
    except Exception as exc:
        return {}, {}, {"openbb_equity_endpoint": str(exc)}

    for symbol in symbols:
        try:
            response = endpoint(symbol=symbol, start_date=start_date, end_date=end_date, provider=provider)
            rows = _response_rows(response)
            metrics = _market_metrics(rows)
            if not metrics:
                errors[symbol] = "OpenBB returned no usable close observations."
                continue
            records[symbol] = {
                "symbol": symbol,
                "last_price": metrics["last_price"],
                "day_change_pct": metrics["day_change_pct"],
                "momentum_30d_pct": metrics["momentum_30d_pct"],
                "above_ma50": metrics["above_ma50"],
                "source": f"OpenBB equity.price.historical:{provider}",
                "as_of": metrics["as_of"],
            }
            history[symbol] = [
                {"date": row.get("date"), "close": row.get("close"), "volume": row.get("volume")}
                for row in metrics["rows"]
            ]
        except Exception as exc:
            errors[symbol] = str(exc)
    return records, history, errors


def fetch_equity_quotes(
    symbols: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    try:
        _configure_provider_caches()
        _patch_openbb_return_annotations()
        from openbb_yfinance.models.equity_quote import YFinanceEquityQuoteFetcher  # type: ignore
    except Exception as exc:
        return {}, {"openbb_yfinance_quote_import": str(exc)}

    async def _fetch() -> Any:
        fetcher = YFinanceEquityQuoteFetcher()
        return await fetcher.fetch_data(params={"symbol": ",".join(symbols)}, credentials={})

    try:
        data = asyncio.run(_fetch())
    except Exception as exc:
        return {}, {"openbb_yfinance_quote": str(exc)}

    records: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    checked_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for item in data or []:
        row = _row_dict(item)
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        last = _round(row.get("last_price"))
        prev = _round(row.get("prev_close"))
        day_change_pct = ((last - prev) / abs(prev) * 100) if last is not None and prev else None
        records[symbol] = {
            "symbol": symbol,
            "last_price": last,
            "day_change_pct": _round(day_change_pct),
            "bid": _round(row.get("bid")),
            "ask": _round(row.get("ask")),
            "open": _round(row.get("open")),
            "high": _round(row.get("high")),
            "low": _round(row.get("low")),
            "prev_close": prev,
            "volume": _round(row.get("volume"), 0),
            "ma50": _round(row.get("ma_50d")),
            "ma200": _round(row.get("ma_200d")),
            "source": "OpenBB yfinance EquityQuoteFetcher",
            "as_of": checked_at,
        }

    missing = sorted(set(symbols) - set(records))
    if missing:
        errors["missing_symbols"] = ",".join(missing)
    return records, errors


def _chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _first_number(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _round(row.get(key), 4)
        if value is not None:
            return value
    return None


def fetch_equity_fundamental_metrics(
    symbols: list[str],
    provider: str = "yfinance",
    chunk_size: int = 24,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    try:
        obb = _load_obb()
        endpoint = obb.equity.fundamental.metrics
    except Exception as exc:
        return {}, {"openbb_fundamental_metrics_import": str(exc)}

    records: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for chunk in _chunked(sorted({symbol.upper() for symbol in symbols if symbol}), chunk_size):
        request_symbols = ",".join(chunk)
        try:
            response = endpoint(symbol=request_symbols, provider=provider)
            rows = _response_rows(response)
        except Exception as exc:
            for symbol in chunk:
                errors[symbol] = str(exc)
            continue

        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            records[symbol] = {
                "symbol": symbol,
                "marketCap": _first_number(row, ("market_cap", "marketCap")),
                "peRatio": _first_number(row, ("pe_ratio", "trailing_pe", "trailingPE")),
                "forwardPE": _first_number(row, ("forward_pe", "forwardPE")),
                "pegRatio": _first_number(row, ("peg_ratio", "pegRatio")),
                "epsTtm": _first_number(row, ("eps_ttm", "epsTTM")),
                "epsForward": _first_number(row, ("eps_forward", "epsForward")),
                "revenueGrowth": _first_number(row, ("revenue_growth", "revenueGrowth")),
                "earningsGrowth": _first_number(row, ("earnings_growth", "earningsGrowth")),
                "grossMargin": _first_number(row, ("gross_margin", "grossMargin")),
                "operatingMargin": _first_number(row, ("operating_margin", "operatingMargin")),
                "profitMargin": _first_number(row, ("profit_margin", "profitMargin")),
                "returnOnEquity": _first_number(row, ("return_on_equity", "returnOnEquity")),
                "debtToEquity": _first_number(row, ("debt_to_equity", "debtToEquity")),
                "currentRatio": _first_number(row, ("current_ratio", "currentRatio")),
                "priceToSales": _first_number(row, ("price_to_sales", "priceToSales")),
                "enterpriseToRevenue": _first_number(row, ("enterprise_to_revenue", "enterpriseToRevenue")),
                "enterpriseToEbitda": _first_number(row, ("enterprise_to_ebitda", "enterpriseToEbitda")),
                "beta": _first_number(row, ("beta",)),
                "source": f"OpenBB equity.fundamental.metrics:{provider}",
                "asOf": _date_text(row.get("period_ending") or row.get("date") or row.get("as_of")),
            }
    return records, errors


def fetch_equity_estimates_consensus(
    symbols: list[str],
    provider: str = "yfinance",
    chunk_size: int = 24,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    try:
        obb = _load_obb()
        endpoint = obb.equity.estimates.consensus
    except Exception as exc:
        return {}, {"openbb_estimates_consensus_import": str(exc)}

    records: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for chunk in _chunked(sorted({symbol.upper() for symbol in symbols if symbol}), chunk_size):
        request_symbols = ",".join(chunk)
        try:
            response = endpoint(symbol=request_symbols, provider=provider)
            rows = _response_rows(response)
        except Exception as exc:
            for symbol in chunk:
                errors[symbol] = str(exc)
            continue

        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            records[symbol] = {
                "symbol": symbol,
                "companyName": row.get("name"),
                "currentPrice": _first_number(row, ("current_price", "currentPrice")),
                "targetHigh": _first_number(row, ("target_high", "targetHigh")),
                "targetLow": _first_number(row, ("target_low", "targetLow")),
                "targetConsensus": _first_number(row, ("target_consensus", "targetConsensus")),
                "targetMedian": _first_number(row, ("target_median", "targetMedian")),
                "recommendation": row.get("recommendation"),
                "recommendationMean": _first_number(row, ("recommendation_mean", "recommendationMean")),
                "numberOfAnalysts": _first_number(row, ("number_of_analysts", "numberOfAnalysts")),
                "currency": row.get("currency"),
                "source": f"OpenBB equity.estimates.consensus:{provider}",
            }
    return records, errors


def _macro_item(
    symbol: str,
    label: str,
    rows: list[tuple[str, float]],
    unit: str,
    source: str,
) -> dict[str, Any] | None:
    rows = [(day, value) for day, value in rows if value is not None]
    rows.sort(key=lambda item: item[0])
    if not rows:
        return None
    values = [value for _, value in rows]
    last = values[-1]
    prev = values[-2] if len(values) >= 2 else None
    base_30d = values[-31] if len(values) >= 31 else values[0]
    return {
        "symbol": symbol,
        "label": label,
        "last": _round(last, 4),
        "dayChangePct": _round(((last - prev) / abs(prev) * 100) if prev else None, 4),
        "momentum30dPct": _round(((last - base_30d) / abs(base_30d) * 100) if base_30d else None, 4),
        "aboveMa50": None,
        "sparkline": [_round(value, 4) for value in values[-40:] if value is not None],
        "unit": unit,
        "asOf": rows[-1][0],
        "source": source,
        "sourceUrl": "OpenBB local provider",
        "dataQuality": "PASS",
        "provider": "OpenBB",
    }


def _historical_macro_from_rows(
    response_fn: Callable[..., Any],
    value_key: str,
    output_symbol: str,
    label: str,
    unit: str,
    source: str,
    **kwargs: Any,
) -> dict[str, Any] | None:
    response = response_fn(**kwargs)
    rows = []
    for row in _response_rows(response):
        value = _round(row.get(value_key) or row.get(value_key.upper()) or row.get("close") or row.get("Close"))
        if value is None:
            continue
        rows.append((_date_text(row.get("date") or row.get("Date") or row.get("index")), value))
    return _macro_item(output_symbol, label, rows, unit, source)


def fetch_true_macro_series(start_date: str, end_date: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    try:
        obb = _load_obb()
    except Exception as exc:
        return [], {"openbb_import": str(exc)}

    items: list[dict[str, Any]] = []
    errors: dict[str, str] = {}

    def _usdcnh_yfinance() -> dict[str, Any] | None:
        request_args = {
            "symbol": "CNH=X",
            "start_date": start_date,
            "end_date": end_date,
            "provider": "yfinance",
        }
        try:
            return _historical_macro_from_rows(
                obb.currency.price.historical,
                "close",
                "USDCNH",
                "USD/CNH",
                "CNH per USD",
                "OpenBB currency.price.historical:yfinance",
                **request_args,
            )
        except Exception:
            end = date.fromisoformat(end_date)
            request_args["start_date"] = (end - timedelta(days=45)).isoformat()
            return _historical_macro_from_rows(
                obb.currency.price.historical,
                "close",
                "USDCNH",
                "USD/CNH",
                "CNH per USD",
                "OpenBB currency.price.historical:yfinance",
                **request_args,
            )

    requests = {
        "VIX": lambda: _historical_macro_from_rows(
            obb.index.price.historical,
            "close",
            "VIX",
            "VIX spot",
            "index points",
            "OpenBB index.price.historical:yfinance",
            symbol="^VIX",
            start_date=start_date,
            end_date=end_date,
            provider="yfinance",
        ),
        "DGS10": lambda: _historical_macro_from_rows(
            obb.index.price.historical,
            "close",
            "DGS10",
            "10Y Treasury Yield",
            "percent",
            "OpenBB index.price.historical:yfinance (^TNX)",
            symbol="^TNX",
            start_date=start_date,
            end_date=end_date,
            provider="yfinance",
        ),
        "USDCNH": _usdcnh_yfinance,
    }
    for key, request in requests.items():
        try:
            item = request()
            if item:
                items.append(item)
            else:
                errors[key] = "OpenBB returned no usable observations."
        except Exception as exc:
            errors[key] = str(exc)
    return items, errors


def diagnostics() -> dict[str, Any]:
    source: dict[str, Any] | None = None
    try:
        import openbb  # type: ignore

        source = _openbb_source(openbb)
        _load_obb()
        version = getattr(openbb, "__version__", "unknown")
        available = True
        error = None
    except Exception as exc:
        version = None
        available = False
        error = str(exc)
    return {
        "available": available,
        "version": version,
        "error": error,
        "source": source or {},
        "checkedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
