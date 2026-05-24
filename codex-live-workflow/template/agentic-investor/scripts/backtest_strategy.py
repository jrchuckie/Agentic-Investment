from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from agentic_investor_common import REPORTS_DIR, RULE_PATH, TRADE_LOG_PATH, now_iso, read_json, today_stamp, write_json
from moomoo_data import fetch_daily_history


ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "reports"


def _default_start() -> str:
    return (date.today() - timedelta(days=540)).isoformat()


def _default_end() -> str:
    return date.today().isoformat()


def _price_frame(history: dict[str, list[dict[str, Any]]]) -> pd.DataFrame:
    frames = []
    for symbol, bars in history.items():
        if not bars:
            continue
        frame = pd.DataFrame(bars)
        frame["date"] = pd.to_datetime(frame["time"]).dt.date
        frame = frame[["date", "close"]].drop_duplicates("date").set_index("date")
        frame = frame.rename(columns={"close": symbol})
        frames.append(frame)
    if not frames:
        raise RuntimeError("No price history returned from moomoo.")
    prices = pd.concat(frames, axis=1).sort_index()
    prices = prices.ffill().dropna(how="all")
    return prices


def _market_state(row: pd.Series, prices: pd.DataFrame) -> str:
    nvda = row.get("NVDA")
    qqq = row.get("QQQ")
    nvda_ma50 = row.get("NVDA_MA50")
    nvda_ma200 = row.get("NVDA_MA200")
    qqq_ma50 = row.get("QQQ_MA50")
    if pd.notna(nvda) and pd.notna(qqq) and pd.notna(nvda_ma50) and pd.notna(qqq_ma50):
        if nvda > nvda_ma50 and qqq > qqq_ma50:
            return "BULL"
    if pd.notna(nvda) and pd.notna(nvda_ma200) and nvda < nvda_ma200:
        return "BEAR"
    if pd.notna(nvda) and pd.notna(nvda_ma50) and pd.notna(nvda_ma200):
        if nvda_ma50 <= nvda <= nvda_ma200:
            return "SIDEWAYS"
    return "SIDEWAYS"


def _target_weights(rules: dict[str, Any], market_state: str, symbols: list[str]) -> dict[str, float]:
    allocation = rules["market_state_rules"].get(market_state, {}).get("allocation", {})
    stock_weight = float(allocation.get("stocks", 0.0))
    portfolio = rules.get("portfolio_rules", {})
    split = portfolio.get("stock_bucket_split", {})
    core_weight = stock_weight * float(split.get("core", 0.7))
    satellite_weight = stock_weight * float(split.get("satellite", 0.3))
    core = [s for s in portfolio.get("tier_core", {}).get("stocks", []) if s in symbols]
    satellite = [s for s in portfolio.get("tier_satellite", {}).get("stocks", []) if s in symbols]
    max_core = float(portfolio.get("tier_core", {}).get("max_per_position", 1.0))
    max_satellite = float(portfolio.get("tier_satellite", {}).get("max_per_position", 1.0))

    weights = {symbol: 0.0 for symbol in symbols}
    if core:
        per = min(core_weight / len(core), max_core)
        for symbol in core:
            weights[symbol] = per
    if satellite:
        per = min(satellite_weight / len(satellite), max_satellite)
        for symbol in satellite:
            weights[symbol] = per
    return weights


def _rebalance_dates(index: pd.Index) -> set[Any]:
    series = pd.Series(index=index, data=index)
    weekly = series.groupby(pd.to_datetime(series.index).to_period("W-FRI")).last()
    return set(weekly.tolist())


def _metrics(equity: pd.Series, returns: pd.Series) -> dict[str, float]:
    total_return = equity.iloc[-1] / equity.iloc[0] - 1
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1
    drawdown = equity / equity.cummax() - 1
    volatility = returns.std() * np.sqrt(252) if len(returns) > 1 else 0.0
    sharpe = (returns.mean() * 252 / volatility) if volatility else 0.0
    return {
        "total_return_pct": total_return * 100,
        "cagr_pct": cagr * 100,
        "max_drawdown_pct": drawdown.min() * 100,
        "annual_volatility_pct": volatility * 100,
        "sharpe_no_rf": sharpe,
        "final_equity": float(equity.iloc[-1]),
    }


def run_backtest(start: str, end: str, initial_capital: float) -> dict[str, Any]:
    rules = read_json(RULE_PATH)
    universe = list(rules.get("universe", []))
    symbols = sorted(set(universe + ["QQQ"]))
    history = fetch_daily_history(symbols, start=start, end=end)
    prices = _price_frame(history)

    for base in ["NVDA", "QQQ"]:
        prices[f"{base}_MA50"] = prices[base].rolling(50).mean()
    prices["NVDA_MA200"] = prices["NVDA"].rolling(200).mean()

    returns = prices[universe].pct_change().fillna(0.0)
    valid_index = prices.index[prices["NVDA_MA200"].notna() & prices["QQQ_MA50"].notna()]
    if len(valid_index) < 10:
        raise RuntimeError("Not enough history after indicator warmup.")

    current_weights = {symbol: 0.0 for symbol in universe}
    rebalance_days = _rebalance_dates(valid_index)
    equity_values = []
    state_rows = []
    turnover = 0.0
    equity = float(initial_capital)
    initialized = False

    for current_date in valid_index:
        row = prices.loc[current_date]
        market_state = _market_state(row, prices)

        if not initialized:
            target = _target_weights(rules, market_state, universe)
            turnover += sum(abs(target[s] - current_weights.get(s, 0.0)) for s in universe)
            current_weights = target
            initialized = True
            equity_values.append((current_date, equity))
            state_rows.append(
                {
                    "date": str(current_date),
                    "market_state": market_state,
                    "equity": equity,
                    "cash_weight": max(0.0, 1.0 - sum(current_weights.values())),
                    "stock_weight": sum(current_weights.values()),
                }
            )
            continue

        day_return = sum(current_weights.get(symbol, 0.0) * float(returns.loc[current_date, symbol]) for symbol in universe)
        equity *= (1 + day_return)
        equity_values.append((current_date, equity))
        state_rows.append(
            {
                "date": str(current_date),
                "market_state": market_state,
                "equity": equity,
                "cash_weight": max(0.0, 1.0 - sum(current_weights.values())),
                "stock_weight": sum(current_weights.values()),
            }
        )
        if current_date in rebalance_days:
            target = _target_weights(rules, market_state, universe)
            turnover += sum(abs(target[s] - current_weights.get(s, 0.0)) for s in universe)
            current_weights = target

    equity_series = pd.Series(
        data=[value for _, value in equity_values],
        index=pd.to_datetime([day for day, _ in equity_values]),
    )
    strategy_returns = equity_series.pct_change().fillna(0.0)
    result_metrics = _metrics(equity_series, strategy_returns)
    state_counts = pd.Series([row["market_state"] for row in state_rows]).value_counts().to_dict()
    latest_weights = current_weights
    latest_prices = {
        symbol: float(prices.loc[valid_index[-1], symbol])
        for symbol in universe
        if symbol in prices.columns and pd.notna(prices.loc[valid_index[-1], symbol])
    }
    return {
        "task": "backtest_strategy",
        "timestamp": now_iso(),
        "start": str(valid_index[0]),
        "end": str(valid_index[-1]),
        "initial_capital": initial_capital,
        "assumptions": {
            "options_bucket": "treated_as_cash_in_v1",
            "rebalance": "weekly, last available trading day ending Friday",
            "transaction_costs": "not modeled",
            "slippage": "not modeled",
            "data_source": "moomoo OpenD historical daily adjusted K-line",
        },
        "metrics": result_metrics,
        "market_state_counts": state_counts,
        "latest_weights": latest_weights,
        "latest_prices": latest_prices,
        "turnover_weight_sum": turnover,
        "equity_curve": [{"date": str(idx.date()), "equity": float(value)} for idx, value in equity_series.items()],
    }


def _pct(value: float) -> str:
    return f"{value:.2f}%"


def write_outputs(result: dict[str, Any]) -> tuple[Path, Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = RESULTS_DIR / f"{today_stamp()}_backtest_strategy.json"
    report_path = RESULTS_DIR / f"{today_stamp()}_backtest_strategy.md"
    write_json(json_path, result)
    metrics = result["metrics"]
    lines = [
        "# Agentic Investor Backtest",
        "",
        f"- Timestamp: {result['timestamp']}",
        f"- Period: {result['start']} to {result['end']}",
        f"- Initial capital: ${result['initial_capital']:,.2f}",
        f"- Final equity: ${metrics['final_equity']:,.2f}",
        f"- Total return: {_pct(metrics['total_return_pct'])}",
        f"- CAGR: {_pct(metrics['cagr_pct'])}",
        f"- Max drawdown: {_pct(metrics['max_drawdown_pct'])}",
        f"- Annual volatility: {_pct(metrics['annual_volatility_pct'])}",
        f"- Sharpe, no risk-free rate: {metrics['sharpe_no_rf']:.2f}",
        "",
        "## Assumptions",
        "",
    ]
    for key, value in result["assumptions"].items():
        lines.append(f"- {key}: {value}")
    lines += [
        "",
        "## Market States",
        "",
    ]
    for key, value in result["market_state_counts"].items():
        lines.append(f"- {key}: {value} trading days")
    lines += [
        "",
        "## Latest Target Weights",
        "",
        "| Symbol | Weight | Latest Price |",
        "|---|---:|---:|",
    ]
    for symbol, weight in sorted(result["latest_weights"].items()):
        price = result["latest_prices"].get(symbol)
        lines.append(f"| {symbol} | {weight * 100:.2f}% | {price:.2f} |" if price else f"| {symbol} | {weight * 100:.2f}% | n/a |")
    lines += [
        "",
        "## Safety",
        "",
        "This backtest is research-only. It did not query accounts, unlock trading, place orders, or modify broker state.",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    log = read_json(TRADE_LOG_PATH, {"records": []})
    log.setdefault("records", []).append(
        {
            "timestamp": result["timestamp"],
            "task": "backtest_strategy",
            "status": "completed",
            "summary": f"Backtested {result['start']} to {result['end']}: total return {_pct(metrics['total_return_pct'])}, max drawdown {_pct(metrics['max_drawdown_pct'])}.",
            "report": str(report_path.relative_to(ROOT)),
            "proposals": [],
        }
    )
    write_json(TRADE_LOG_PATH, log)
    return report_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run advisory-only agentic investor backtest.")
    parser.add_argument("--start", default=_default_start(), help="Start date, YYYY-MM-DD. Defaults to ~540 calendar days ago.")
    parser.add_argument("--end", default=_default_end(), help="End date, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--initial-capital", type=float, default=135000.0)
    args = parser.parse_args()
    result = run_backtest(args.start, args.end, args.initial_capital)
    report_path, json_path = write_outputs(result)
    print(json.dumps({"status": "completed", "report": str(report_path), "json": str(json_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
