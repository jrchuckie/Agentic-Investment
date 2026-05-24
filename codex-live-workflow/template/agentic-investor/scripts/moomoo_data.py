from __future__ import annotations

import os
import socket
from datetime import date, timedelta
from typing import Any

from agentic_investor_common import enable_vendor_python


def _opend_reachable(host: str, port: int, timeout_s: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _prepare_moomoo_imports():
    enable_vendor_python()
    os.environ.setdefault(
        "MOOMOO_LOG_DIR",
        str((__import__("pathlib").Path(__file__).resolve().parents[1] / ".moomoo-logs")),
    )
    from moomoo import AuType, KLType, OpenQuoteContext, RET_OK, Session

    return AuType, KLType, OpenQuoteContext, RET_OK, Session


def to_us_code(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if "." in symbol:
        return symbol
    return f"US.{symbol}"


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row.get(key, default)
    except AttributeError:
        return getattr(row, key, default)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _snapshot_row(row: Any) -> dict[str, Any]:
    last = _float(_row_value(row, "last_price"))
    prev = _float(_row_value(row, "prev_close_price"))
    day_change_pct = ((last - prev) / prev * 100) if prev else None
    return {
        "code": _row_value(row, "code", ""),
        "symbol": str(_row_value(row, "code", "")).split(".")[-1],
        "name": _row_value(row, "name", ""),
        "last_price": last,
        "prev_close": prev,
        "open": _float(_row_value(row, "open_price")),
        "high": _float(_row_value(row, "high_price")),
        "low": _float(_row_value(row, "low_price")),
        "volume": int(_float(_row_value(row, "volume"), 0)),
        "turnover": _float(_row_value(row, "turnover")),
        "bid": _float(_row_value(row, "bid_price")),
        "ask": _float(_row_value(row, "ask_price")),
        "day_change_pct": day_change_pct,
    }


def _bars_from_frame(data: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if data is None or len(data) == 0:
        return rows
    for i in range(len(data)):
        row = data.iloc[i] if hasattr(data, "iloc") else data[i]
        rows.append(
            {
                "time": str(_row_value(row, "time_key", "")),
                "close": _float(_row_value(row, "close")),
                "open": _float(_row_value(row, "open")),
                "high": _float(_row_value(row, "high")),
                "low": _float(_row_value(row, "low")),
                "volume": int(_float(_row_value(row, "volume"), 0)),
            }
        )
    return rows


def _mean(values: list[float]) -> float | None:
    values = [v for v in values if v]
    if not values:
        return None
    return sum(values) / len(values)


def _momentum(closes: list[float], bars: int) -> float | None:
    if len(closes) <= bars or closes[-bars - 1] == 0:
        return None
    return (closes[-1] / closes[-bars - 1] - 1) * 100


def _metrics(snapshot: dict[str, Any], bars: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [float(bar["close"]) for bar in bars if bar.get("close")]
    volumes = [float(bar["volume"]) for bar in bars if bar.get("volume")]
    ma50 = _mean(closes[-50:])
    ma200 = _mean(closes[-200:])
    last = snapshot.get("last_price") or (closes[-1] if closes else 0)
    return {
        **snapshot,
        "bars": len(bars),
        "last_bar_time": bars[-1]["time"] if bars else "",
        "momentum_30d_pct": _momentum(closes, 30),
        "momentum_60d_pct": _momentum(closes, 60),
        "ma50": ma50,
        "ma200": ma200,
        "avg_volume_20d": _mean(volumes[-20:]),
        "above_ma50": bool(ma50 and last > ma50),
        "above_ma200": bool(ma200 and last > ma200),
    }


def fetch_research_snapshot(symbols: list[str], lookback_days: int = 420) -> dict[str, Any]:
    AuType, KLType, OpenQuoteContext, RET_OK, Session = _prepare_moomoo_imports()
    codes = [to_us_code(symbol) for symbol in symbols]
    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    end = date.today().isoformat()
    host = "127.0.0.1"
    port = 11111
    if not _opend_reachable(host, port):
        raise RuntimeError(f"OpenD not reachable at {host}:{port}. Is OpenD running?")
    ctx = OpenQuoteContext(host=host, port=port, ai_type=1)
    try:
        ret, snapshot_data = ctx.get_market_snapshot(codes)
        if ret != RET_OK:
            raise RuntimeError(f"get_market_snapshot failed: {snapshot_data}")
        snapshots: dict[str, dict[str, Any]] = {}
        for i in range(len(snapshot_data)):
            row = snapshot_data.iloc[i] if hasattr(snapshot_data, "iloc") else snapshot_data[i]
            parsed = _snapshot_row(row)
            snapshots[parsed["code"]] = parsed

        records: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        for code in codes:
            try:
                ret, data, _ = ctx.request_history_kline(
                    code,
                    start=start,
                    end=end,
                    ktype=KLType.K_DAY,
                    autype=AuType.QFQ,
                    max_count=1000,
                    session=Session.NONE,
                )
                if ret != RET_OK:
                    raise RuntimeError(str(data))
                records.append(_metrics(snapshots.get(code, {"code": code, "symbol": code.split(".")[-1]}), _bars_from_frame(data)))
            except Exception as exc:  # keep the scan resilient; report data gaps.
                errors[code] = str(exc)

        return {
            "source": "moomoo OpenD",
            "host": "127.0.0.1",
            "port": 11111,
            "lookback_start": start,
            "lookback_end": end,
            "records": records,
            "errors": errors,
        }
    finally:
        ctx.close()


def fetch_daily_history(symbols: list[str], start: str, end: str) -> dict[str, list[dict[str, Any]]]:
    AuType, KLType, OpenQuoteContext, RET_OK, Session = _prepare_moomoo_imports()
    host = "127.0.0.1"
    port = 11111
    if not _opend_reachable(host, port):
        raise RuntimeError(f"OpenD not reachable at {host}:{port}. Is OpenD running?")
    ctx = OpenQuoteContext(host=host, port=port, ai_type=1)
    try:
        history: dict[str, list[dict[str, Any]]] = {}
        for symbol in symbols:
            code = to_us_code(symbol)
            ret, data, page_req_key = ctx.request_history_kline(
                code,
                start=start,
                end=end,
                ktype=KLType.K_DAY,
                autype=AuType.QFQ,
                max_count=1000,
                session=Session.NONE,
            )
            if ret != RET_OK:
                raise RuntimeError(f"{code}: {data}")
            bars = _bars_from_frame(data)
            while page_req_key is not None:
                ret, data, page_req_key = ctx.request_history_kline(
                    code,
                    start=start,
                    end=end,
                    ktype=KLType.K_DAY,
                    autype=AuType.QFQ,
                    max_count=1000,
                    page_req_key=page_req_key,
                    session=Session.NONE,
                )
                if ret != RET_OK:
                    raise RuntimeError(f"{code}: {data}")
                bars.extend(_bars_from_frame(data))
            history[code.split(".")[-1]] = bars
        return history
    finally:
        ctx.close()


def fetch_daily_history_resilient(symbols: list[str], start: str, end: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    AuType, KLType, OpenQuoteContext, RET_OK, Session = _prepare_moomoo_imports()
    host = "127.0.0.1"
    port = 11111
    if not _opend_reachable(host, port):
        return {}, {f"{host}:{port}": f"OpenD not reachable at {host}:{port}. Is OpenD running?"}
    ctx = OpenQuoteContext(host=host, port=port, ai_type=1)
    history: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    try:
        for symbol in symbols:
            code = to_us_code(symbol)
            try:
                ret, data, page_req_key = ctx.request_history_kline(
                    code,
                    start=start,
                    end=end,
                    ktype=KLType.K_DAY,
                    autype=AuType.QFQ,
                    max_count=1000,
                    session=Session.NONE,
                )
                if ret != RET_OK:
                    raise RuntimeError(str(data))
                bars = _bars_from_frame(data)
                while page_req_key is not None:
                    ret, data, page_req_key = ctx.request_history_kline(
                        code,
                        start=start,
                        end=end,
                        ktype=KLType.K_DAY,
                        autype=AuType.QFQ,
                        max_count=1000,
                        page_req_key=page_req_key,
                        session=Session.NONE,
                    )
                    if ret != RET_OK:
                        raise RuntimeError(str(data))
                    bars.extend(_bars_from_frame(data))
                if bars:
                    history[code.split(".")[-1]] = bars
                else:
                    errors[code] = "No daily bars returned."
            except Exception as exc:
                errors[code] = str(exc)
        return history, errors
    finally:
        ctx.close()
