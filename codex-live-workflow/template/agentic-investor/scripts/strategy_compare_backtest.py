from __future__ import annotations

import json
import math
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRICE_CACHE_DIR = PROJECT_ROOT / "data" / "backtest_cache" / "prices"
BACKTEST_DIR = PROJECT_ROOT / "data" / "backtests"
REPORT_DIR = PROJECT_ROOT / "reports"


AI_CORE = [
    "NVDA",
    "AMD",
    "AVGO",
    "TSM",
    "ARM",
    "MRVL",
    "ALAB",
    "MU",
    "SNDK",
    "WDC",
    "STX",
    "LRCX",
    "MPWR",
    "NXPI",
    "ON",
    "SMH",
]
AI_INFRA = [
    "ANET",
    "VRT",
    "COHR",
    "LITE",
    "FN",
    "CLS",
    "CRDO",
    "CRWV",
    "NBIS",
    "APLD",
    "IREN",
    "BE",
    "GEV",
    "PWR",
    "ETN",
    "CEG",
    "VST",
    "NRG",
    "SMCI",
    "DELL",
]
AI_SOFTWARE = [
    "PLTR",
    "TEM",
    "APP",
    "SNOW",
    "DDOG",
    "GTLB",
    "CRWD",
    "PANW",
    "MSFT",
    "GOOG",
    "GOOGL",
    "META",
    "ORCL",
    "AMZN",
    "RDDT",
    "SHOP",
]
ACTIVIST_EVENT = ["PINS", "TXN", "INTC", "BILL", "HOOD", "WBD"]
BENCHMARKS = ["QQQ", "SMH", "SPY"]


@dataclass(frozen=True)
class StrategySpec:
    key: str
    name: str
    description: str
    universe: tuple[str, ...]
    score_model: str
    top_n: int
    exposure: float
    max_position: float
    rebalance: str = "W-FRI"
    risk_filter: bool = True


@dataclass
class PriceSeries:
    symbol: str
    records: list[dict[str, Any]]
    by_date: dict[date, dict[str, Any]]
    positions: dict[date, int]


STRATEGIES: tuple[StrategySpec, ...] = (
    StrategySpec(
        key="trend_ai_top5",
        name="AI主线趋势动量 Top5",
        description="每周选择 AI 算力、基础设施和应用层里相对强度最好的 5 个标的。",
        universe=tuple(dict.fromkeys(AI_CORE + AI_INFRA + AI_SOFTWARE)),
        score_model="trend",
        top_n=5,
        exposure=0.95,
        max_position=0.22,
    ),
    StrategySpec(
        key="concentrated_ai_top3",
        name="高集中 AI 强势 Top3",
        description="只买最强的 3 个 AI 主线候选，收益弹性高，但回撤也更大。",
        universe=tuple(dict.fromkeys(AI_CORE + AI_INFRA + AI_SOFTWARE)),
        score_model="trend",
        top_n=3,
        exposure=1.0,
        max_position=0.34,
    ),
    StrategySpec(
        key="dip_buy_ai_leaders",
        name="AI 龙头回撤承接",
        description="只在中长期趋势未破、短线回撤出现时尝试承接 AI 龙头。",
        universe=tuple(dict.fromkeys(AI_CORE + AI_INFRA)),
        score_model="dip",
        top_n=4,
        exposure=0.8,
        max_position=0.24,
    ),
    StrategySpec(
        key="low_vol_compounders",
        name="低波趋势复利",
        description="偏向趋势稳定、波动较低的 AI 平台和复利型股票，降低账户震荡。",
        universe=tuple(dict.fromkeys(AI_CORE + AI_SOFTWARE + ["QQQ", "SMH"])),
        score_model="low_vol_trend",
        top_n=5,
        exposure=0.9,
        max_position=0.2,
    ),
    StrategySpec(
        key="ai_infra_equal_risk_on",
        name="AI 基础设施等权篮子",
        description="在风险偏好正常时等权持有 AI 供电、冷却、网络和数据中心建设链。",
        universe=tuple(dict.fromkeys(AI_INFRA)),
        score_model="equal_weight",
        top_n=8,
        exposure=0.8,
        max_position=0.13,
    ),
    StrategySpec(
        key="activist_event_watch",
        name="激进投资事件篮子",
        description="用当前激进投资观察池做价格验证。注意：这不是点时 13D 事件回测。",
        universe=tuple(ACTIVIST_EVENT),
        score_model="trend",
        top_n=3,
        exposure=0.45,
        max_position=0.18,
    ),
    StrategySpec(
        key="benchmark_qqq",
        name="基准：QQQ 买入持有",
        description="纳指 100 ETF 买入持有。",
        universe=("QQQ",),
        score_model="buy_hold",
        top_n=1,
        exposure=1.0,
        max_position=1.0,
        risk_filter=False,
    ),
    StrategySpec(
        key="benchmark_smh",
        name="基准：SMH 买入持有",
        description="半导体 ETF 买入持有。",
        universe=("SMH",),
        score_model="buy_hold",
        top_n=1,
        exposure=1.0,
        max_position=1.0,
        risk_filter=False,
    ),
)


def _safe_symbol(symbol: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in symbol.upper())


def _cache_path(symbol: str, start: date, end: date) -> Path:
    return PRICE_CACHE_DIR / f"{_safe_symbol(symbol)}_{start.isoformat()}_{end.isoformat()}.json"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def _to_epoch(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def _fetch_yahoo_chart(symbol: str, start: date, end: date) -> dict[str, Any]:
    encoded = urllib.parse.quote(symbol, safe="")
    params = urllib.parse.urlencode(
        {
            "period1": _to_epoch(start),
            "period2": _to_epoch(end + timedelta(days=1)),
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
    )
    req = urllib.request.Request(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?{params}",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def _list_value(values: Any, idx: int) -> float | int | None:
    if not isinstance(values, list) or idx >= len(values) or values[idx] is None:
        return None
    value = values[idx]
    return value if isinstance(value, int) else float(value)


def _records_from_chart(symbol: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        error = payload.get("chart", {}).get("error")
        raise ValueError(f"Yahoo chart missing result for {symbol}: {error}")
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adjclose = ((result.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
    close = quote.get("close") or []
    records: list[dict[str, Any]] = []
    for idx, ts in enumerate(timestamps):
        value = adjclose[idx] if idx < len(adjclose) and adjclose[idx] is not None else None
        if value is None and idx < len(close):
            value = close[idx]
        if value is None:
            continue
        records.append(
            {
                "date": datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat(),
                "open": _list_value(quote.get("open"), idx),
                "high": _list_value(quote.get("high"), idx),
                "low": _list_value(quote.get("low"), idx),
                "close": _list_value(close, idx),
                "adj_close": float(value),
                "volume": _list_value(quote.get("volume"), idx),
            }
        )
    if not records:
        raise ValueError(f"No usable daily bars for {symbol}")
    return records


def _rolling_mean(values: list[float], idx: int, window: int) -> float | None:
    if idx + 1 < window:
        return None
    chunk = values[idx + 1 - window : idx + 1]
    return sum(chunk) / window


def _rolling_vol(values: list[float], idx: int, window: int) -> float | None:
    if idx + 1 < window:
        return None
    chunk = values[idx + 1 - window : idx + 1]
    if len(chunk) < 2:
        return None
    return statistics.pstdev(chunk) * math.sqrt(252)


def _prepare_series(symbol: str, records: list[dict[str, Any]]) -> PriceSeries:
    prepared = sorted(records, key=lambda item: item["date"])
    closes = [float(item["adj_close"]) for item in prepared]
    previous_close: float | None = None
    for idx, item in enumerate(prepared):
        item["date"] = date.fromisoformat(item["date"]) if isinstance(item["date"], str) else item["date"]
        item["ret"] = 0.0 if previous_close in (None, 0) else closes[idx] / previous_close - 1.0
        item["ma20"] = _rolling_mean(closes, idx, 20)
        item["ma50"] = _rolling_mean(closes, idx, 50)
        item["ma200"] = _rolling_mean(closes, idx, 200)
        item["vol63"] = _rolling_vol([float(r.get("ret", 0.0)) for r in prepared], idx, 63)
        previous_close = closes[idx]
    by_date = {item["date"]: item for item in prepared}
    positions = {item["date"]: idx for idx, item in enumerate(prepared)}
    return PriceSeries(symbol=symbol, records=prepared, by_date=by_date, positions=positions)


def fetch_price_history(symbol: str, start: date, end: date, *, cache_only: bool = False) -> tuple[PriceSeries, dict[str, Any]]:
    path = _cache_path(symbol, start, end)
    meta = {"symbol": symbol, "cachePath": str(path), "cacheHit": False, "fetched": False}
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload.get("records") or []
        meta["cacheHit"] = True
    else:
        if cache_only:
            raise FileNotFoundError(f"No cached price data for {symbol}")
        payload = _fetch_yahoo_chart(symbol, start, end)
        records = _records_from_chart(symbol, payload)
        _write_json(
            path,
            {
                "symbol": symbol,
                "source": "yahoo_chart_public",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "fetchedAt": datetime.now(timezone.utc).isoformat(),
                "records": records,
            },
        )
        meta["fetched"] = True
        time.sleep(0.2)
    return _prepare_series(symbol, records), meta


def _universe_symbols() -> list[str]:
    symbols: list[str] = []
    for spec in STRATEGIES:
        symbols.extend(spec.universe)
    symbols.extend(BENCHMARKS)
    return sorted(dict.fromkeys(symbols))


def load_price_panel(start: date, end: date, *, cache_only: bool = False) -> tuple[dict[str, PriceSeries], dict[str, Any]]:
    panel: dict[str, PriceSeries] = {}
    sources: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for symbol in _universe_symbols():
        try:
            series, meta = fetch_price_history(symbol, start, end, cache_only=cache_only)
            if len(series.records) < 65:
                errors[symbol] = f"insufficient history: {len(series.records)} bars"
                continue
            panel[symbol] = series
            sources.append(meta)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, FileNotFoundError) as exc:
            errors[symbol] = str(exc)
    return panel, {
        "moomooUsed": False,
        "primarySource": "local cache + Yahoo public chart endpoint",
        "cacheHits": sum(1 for item in sources if item.get("cacheHit")),
        "fetched": sum(1 for item in sources if item.get("fetched")),
        "loadedSymbols": sorted(panel),
        "missingSymbols": errors,
    }


def _rebalance_dates(dates: list[date], freq: str) -> set[date]:
    groups: dict[tuple[int, int], date] = {}
    for day in dates:
        if freq == "W-FRI":
            year, week, _weekday = day.isocalendar()
            key = (year, week)
        else:
            key = (day.year, day.month)
        groups[key] = day
    return set(groups.values())


def _value_at(series: PriceSeries | None, current_date: date, column: str) -> float | None:
    if series is None:
        return None
    record = series.by_date.get(current_date)
    if record is None:
        return None
    value = record.get(column)
    if value is None:
        return None
    return float(value)


def _return_between(series: PriceSeries, current_date: date, days: int) -> float | None:
    loc = series.positions.get(current_date)
    if loc is None or loc < days:
        return None
    current = float(series.records[loc]["adj_close"])
    previous = float(series.records[loc - days]["adj_close"])
    if previous <= 0:
        return None
    return current / previous - 1.0


def _relative_return(panel: dict[str, PriceSeries], symbol: str, current_date: date, days: int) -> float:
    own = _return_between(panel[symbol], current_date, days)
    qqq = _return_between(panel["QQQ"], current_date, days) if "QQQ" in panel else None
    if own is None or qqq is None:
        return 0.0
    return own - qqq


def _risk_on(panel: dict[str, PriceSeries], current_date: date) -> bool:
    qqq = panel.get("QQQ")
    smh = panel.get("SMH")
    qqq_price = _value_at(qqq, current_date, "adj_close")
    qqq_ma50 = _value_at(qqq, current_date, "ma50")
    qqq_ma200 = _value_at(qqq, current_date, "ma200")
    smh_price = _value_at(smh, current_date, "adj_close")
    smh_ma50 = _value_at(smh, current_date, "ma50")
    if qqq_price is None or qqq_ma200 is None:
        return True
    if qqq_price < qqq_ma200:
        return False
    if qqq_ma50 is not None and qqq_price >= qqq_ma50:
        return True
    return bool(smh_price is not None and smh_ma50 is not None and smh_price >= smh_ma50)


def _score_symbol(panel: dict[str, PriceSeries], symbol: str, current_date: date, model: str) -> float | None:
    series = panel.get(symbol)
    if series is None or current_date not in series.by_date:
        return None
    price = _value_at(series, current_date, "adj_close")
    ma50 = _value_at(series, current_date, "ma50")
    ma200 = _value_at(series, current_date, "ma200")
    vol63 = _value_at(series, current_date, "vol63") or 0.0
    ret5 = _return_between(series, current_date, 5)
    ret20 = _return_between(series, current_date, 20)
    ret63 = _return_between(series, current_date, 63)
    ret126 = _return_between(series, current_date, 126)
    rel63 = _relative_return(panel, symbol, current_date, 63)
    if price is None:
        return None
    if model == "buy_hold":
        return 1.0
    if model == "equal_weight":
        if ma200 is None or price < ma200:
            return None
        return 1.0
    if ma50 is None or ma200 is None or ret63 is None or ret126 is None:
        return None
    if model == "trend":
        if price < ma50 or price < ma200 or ret63 <= 0 or ret126 <= 0:
            return None
        return 0.45 * ret63 + 0.35 * ret126 + 0.15 * (ret20 or 0.0) + 0.05 * rel63
    if model == "dip":
        price_to_ma50 = price / ma50 - 1.0
        if price < ma200 or ret126 <= 0 or ret5 is None:
            return None
        if ret5 > -0.015 or price_to_ma50 < -0.12 or price_to_ma50 > 0.10:
            return None
        return 0.45 * ret126 + 0.25 * max(0.0, -ret5) + 0.20 * rel63 - 0.10 * abs(price_to_ma50)
    if model == "low_vol_trend":
        if price < ma50 or price < ma200 or ret126 <= 0:
            return None
        return 0.55 * ret126 + 0.25 * (ret63 or 0.0) + 0.20 * rel63 - 0.35 * vol63
    return None


def _target_weights(panel: dict[str, PriceSeries], spec: StrategySpec, current_date: date) -> dict[str, float]:
    if spec.risk_filter and not _risk_on(panel, current_date):
        return {"QQQ": 0.35} if "QQQ" in panel else {}
    scored: list[tuple[str, float]] = []
    for symbol in spec.universe:
        score = _score_symbol(panel, symbol, current_date, spec.score_model)
        if score is not None and math.isfinite(score):
            scored.append((symbol, score))
    if not scored:
        return {}
    scored.sort(key=lambda item: item[1], reverse=True)
    selected = scored[: spec.top_n]
    weight = min(spec.exposure / len(selected), spec.max_position)
    return {symbol: weight for symbol, _score in selected}


def run_strategy(
    panel: dict[str, PriceSeries],
    spec: StrategySpec,
    dates: list[date],
    *,
    transaction_cost_bps: float,
) -> dict[str, Any]:
    rebalance_days = _rebalance_dates(dates, spec.rebalance)
    weights: dict[str, float] = {}
    equity = 1.0
    previous_equity = 1.0
    rows: list[dict[str, Any]] = []
    turnover_sum = 0.0
    rebalance_count = 0

    for idx, current_date in enumerate(dates):
        day_ret = 0.0
        for symbol, weight in weights.items():
            record = panel.get(symbol).by_date.get(current_date) if symbol in panel else None
            if record is not None:
                day_ret += weight * float(record.get("ret", 0.0))
        equity *= 1.0 + day_ret

        if current_date in rebalance_days and idx > 0:
            target = _target_weights(panel, spec, current_date)
            all_symbols = sorted(set(weights) | set(target))
            turnover = sum(abs(target.get(symbol, 0.0) - weights.get(symbol, 0.0)) for symbol in all_symbols)
            equity *= 1.0 - turnover * transaction_cost_bps / 10000.0
            turnover_sum += turnover
            weights = target
            rebalance_count += 1

        rows.append(
            {
                "date": current_date.isoformat(),
                "equity": equity,
                "dailyReturn": equity / previous_equity - 1.0 if previous_equity else 0.0,
                "positions": sorted(weights),
                "grossExposure": sum(weights.values()),
            }
        )
        previous_equity = equity

    return {
        "key": spec.key,
        "name": spec.name,
        "description": spec.description,
        "universeSize": len(spec.universe),
        "scoreModel": spec.score_model,
        "topN": spec.top_n,
        "exposure": spec.exposure,
        "maxPosition": spec.max_position,
        "rebalance": spec.rebalance,
        "riskFilter": spec.risk_filter,
        "metrics": _metrics(rows, turnover_sum, rebalance_count),
        "lastPositions": rows[-1]["positions"] if rows else [],
        "history": rows,
    }


def _metrics(rows: list[dict[str, Any]], turnover_sum: float, rebalance_count: int) -> dict[str, Any]:
    if not rows:
        return {}
    equities = [float(row["equity"]) for row in rows]
    daily = [float(row["dailyReturn"]) for row in rows]
    days = max(1, len(rows))
    total_return = equities[-1] - 1.0
    cagr = equities[-1] ** (252.0 / days) - 1.0
    peak = equities[0]
    drawdowns: list[float] = []
    for value in equities:
        peak = max(peak, value)
        drawdowns.append(value / peak - 1.0)
    max_dd = min(drawdowns)
    annual_vol = statistics.pstdev(daily) * math.sqrt(252) if len(daily) > 1 else 0.0
    sharpe = cagr / annual_vol if annual_vol > 0 else None
    calmar = cagr / abs(max_dd) if max_dd < 0 else None
    return {
        "totalReturnPct": round(total_return * 100, 2),
        "cagrPct": round(cagr * 100, 2),
        "maxDrawdownPct": round(max_dd * 100, 2),
        "annualVolPct": round(annual_vol * 100, 2),
        "sharpeNoRf": round(sharpe, 2) if sharpe is not None else None,
        "calmar": round(calmar, 2) if calmar is not None else None,
        "positiveDayRatePct": round(sum(1 for x in daily if x > 0) / len(daily) * 100, 2),
        "finalEquity": round(equities[-1], 4),
        "turnoverSum": round(turnover_sum, 2),
        "annualTurnover": round(turnover_sum / max(days / 252.0, 1e-9), 2),
        "rebalanceCount": rebalance_count,
        "avgPositions": round(sum(len(row["positions"]) for row in rows) / len(rows), 2),
    }


def _common_dates(panel: dict[str, PriceSeries], start: date, end: date) -> list[date]:
    if "QQQ" not in panel:
        raise RuntimeError("QQQ history is required as the trading calendar.")
    dates = [record["date"] for record in panel["QQQ"].records if start <= record["date"] <= end]
    dates = dates[205:]
    if len(dates) < 80:
        raise RuntimeError("Not enough common trading dates after indicator warmup.")
    return dates


def build_strategy_compare_backtest(
    *,
    start: date | None = None,
    end: date | None = None,
    transaction_cost_bps: float = 5.0,
    cache_only: bool = False,
) -> dict[str, Any]:
    end = end or date.today()
    start = start or date(end.year - 2, 1, 1)
    panel, data_policy = load_price_panel(start, end, cache_only=cache_only)
    dates = _common_dates(panel, start, end)
    results = [run_strategy(panel, spec, dates, transaction_cost_bps=transaction_cost_bps) for spec in STRATEGIES]
    ranked = sorted(
        results,
        key=lambda item: (
            item["metrics"].get("sharpeNoRf") if item["metrics"].get("sharpeNoRf") is not None else -999,
            item["metrics"].get("cagrPct") if item["metrics"].get("cagrPct") is not None else -999,
        ),
        reverse=True,
    )
    return {
        "task": "strategy_compare_backtest",
        "status": "PASS" if results else "FAIL",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "range": {"start": start.isoformat(), "end": end.isoformat(), "tradingDays": len(dates)},
        "transactionCostBps": transaction_cost_bps,
        "dataPolicy": data_policy,
        "warnings": [
            "本次只回测历史价格能干净验证的策略；新闻、13D、Prism、基金经理披露和当前估值暂不做历史收益归因，避免未来函数。",
            "activist_event_watch 使用当前事件观察池做价格验证，不等同于真实点时 13D/13F 事件策略。",
            "本任务不读取真实账户、不连接 moomoo、不下任何真实订单。",
        ],
        "strategies": results,
        "ranking": [
            {
                "key": item["key"],
                "name": item["name"],
                "cagrPct": item["metrics"].get("cagrPct"),
                "maxDrawdownPct": item["metrics"].get("maxDrawdownPct"),
                "sharpeNoRf": item["metrics"].get("sharpeNoRf"),
                "calmar": item["metrics"].get("calmar"),
                "lastPositions": item.get("lastPositions", []),
            }
            for item in ranked
        ],
    }


def write_outputs(result: dict[str, Any]) -> tuple[Path, Path]:
    today = date.today().isoformat()
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    latest = BACKTEST_DIR / "latest_strategy_compare.json"
    dated = BACKTEST_DIR / f"{today}_strategy_compare.json"
    report = REPORT_DIR / f"{today}_strategy_compare.md"
    _write_json(dated, result)
    _write_json(latest, result)
    report.write_text(_render_report(result), encoding="utf-8")
    return report, latest


def _render_report(result: dict[str, Any]) -> str:
    lines = [
        "# Strategy Compare Backtest",
        "",
        f"- Status: {result.get('status')}",
        f"- Range: {result.get('range', {}).get('start')} to {result.get('range', {}).get('end')} ({result.get('range', {}).get('tradingDays')} trading days)",
        f"- Transaction cost: {result.get('transactionCostBps')} bps one-way",
        f"- Moomoo API used: {result.get('dataPolicy', {}).get('moomooUsed')}",
        f"- Data source: {result.get('dataPolicy', {}).get('primarySource')}",
        f"- Cache hits / fetched: {result.get('dataPolicy', {}).get('cacheHits')} / {result.get('dataPolicy', {}).get('fetched')}",
        "",
        "## 排名",
        "",
        "| Rank | Strategy | CAGR | Max DD | Sharpe | Calmar | Current basket |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for idx, item in enumerate(result.get("ranking", []), start=1):
        lines.append(
            "| {rank} | {name} | {cagr}% | {mdd}% | {sharpe} | {calmar} | {positions} |".format(
                rank=idx,
                name=item.get("name"),
                cagr=item.get("cagrPct"),
                mdd=item.get("maxDrawdownPct"),
                sharpe=item.get("sharpeNoRf"),
                calmar=item.get("calmar"),
                positions=", ".join(item.get("lastPositions") or []),
            )
        )
    lines += ["", "## 策略定义", ""]
    for item in result.get("strategies", []):
        metrics = item.get("metrics", {})
        lines += [
            f"### {item.get('name')}",
            "",
            f"- 逻辑: {item.get('description')}",
            f"- Score model: {item.get('scoreModel')} / Top N: {item.get('topN')} / Exposure: {item.get('exposure')}",
            f"- CAGR: {metrics.get('cagrPct')}% / Max DD: {metrics.get('maxDrawdownPct')}% / Sharpe: {metrics.get('sharpeNoRf')}",
            f"- 当前篮子: {', '.join(item.get('lastPositions') or [])}",
            "",
        ]
    lines += ["## 数据与安全说明", ""]
    for warning in result.get("warnings", []):
        lines.append(f"- {warning}")
    missing = result.get("dataPolicy", {}).get("missingSymbols", {})
    if missing:
        lines += ["", "## 缺失或不足数据", ""]
        for symbol, reason in sorted(missing.items()):
            lines.append(f"- {symbol}: {reason}")
    return "\n".join(lines) + "\n"


def main() -> int:
    result = build_strategy_compare_backtest()
    report, latest = write_outputs(result)
    print(
        json.dumps(
            {
                "task": "strategy_compare_backtest",
                "status": result.get("status"),
                "moomooUsed": result.get("dataPolicy", {}).get("moomooUsed"),
                "strategies": len(result.get("strategies", [])),
                "report": str(report),
                "latest": str(latest),
                "topStrategy": (result.get("ranking") or [{}])[0].get("name"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
