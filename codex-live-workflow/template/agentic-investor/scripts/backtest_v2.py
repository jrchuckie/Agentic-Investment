from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from agentic_investor_common import REPORTS_DIR, RULE_PATH, TRADE_LOG_PATH, now_iso, read_json, today_stamp, write_json
from moomoo_data import fetch_daily_history_resilient


ROOT = Path(__file__).resolve().parents[1]
FUND_HOLDINGS_LATEST_PATH = ROOT / "data" / "fund_holdings" / "latest.json"


V2_CANDIDATES = [
    "NVDA", "GOOG", "AMZN", "MSFT", "PANW", "CRWD", "PLTR", "META",
    "AVGO", "AMD", "TSM", "MU", "SNDK", "ARM", "MRVL", "SMCI",
    "ANET", "ORCL", "DELL", "SMH",
]


@dataclass(frozen=True)
class Variant:
    name: str
    lookback_fast: int
    lookback_slow: int
    top_n: int
    gross_exposure: float
    max_position: float
    qqq_floor: float = 0.0
    smh_floor: float = 0.0
    min_candidates: int = 3
    risk_off_exposure: float = 0.35
    risk_off_asset: str = "QQQ"


VARIANTS = [
    Variant("v2_top5_90pct_rs", 63, 126, 5, 0.90, 0.25),
    Variant("v2_top4_100pct_rs", 63, 126, 4, 1.00, 0.30),
    Variant("v2_top3_100pct_rs", 63, 126, 3, 1.00, 0.35),
    Variant("v2_smh30_top4_70pct", 63, 126, 4, 1.00, 0.25, smh_floor=0.30),
    Variant("v2_top3_130pct_margin_sim", 63, 126, 3, 1.30, 0.45),
]


def _default_start() -> str:
    return (date.today() - timedelta(days=900)).isoformat()


def _default_end() -> str:
    return date.today().isoformat()


def _price_frame(history: dict[str, list[dict[str, Any]]]) -> pd.DataFrame:
    frames = []
    for symbol, bars in history.items():
        frame = pd.DataFrame(bars)
        if frame.empty:
            continue
        frame["date"] = pd.to_datetime(frame["time"]).dt.date
        frame = frame[["date", "close"]].drop_duplicates("date").set_index("date")
        frame = frame.rename(columns={"close": symbol})
        frames.append(frame)
    if not frames:
        raise RuntimeError("No usable historical prices returned from moomoo.")
    prices = pd.concat(frames, axis=1).sort_index().ffill()
    return prices


def _weekly_rebalance_dates(index: pd.Index) -> set[Any]:
    series = pd.Series(index=index, data=index)
    return set(series.groupby(pd.to_datetime(series.index).to_period("W-FRI")).last().tolist())


def _risk_on(row: pd.Series) -> bool:
    qqq = row.get("QQQ")
    smh = row.get("SMH")
    qqq_ma50 = row.get("QQQ_MA50")
    qqq_ma200 = row.get("QQQ_MA200")
    smh_ma50 = row.get("SMH_MA50")
    if pd.isna(qqq) or pd.isna(qqq_ma50) or pd.isna(qqq_ma200):
        return False
    if qqq < qqq_ma200:
        return False
    if pd.notna(smh) and pd.notna(smh_ma50):
        return qqq > qqq_ma50 or smh > smh_ma50
    return qqq > qqq_ma50


def _candidate_score(
    prices: pd.DataFrame,
    current_date: Any,
    symbol: str,
    variant: Variant,
    fund_scores: dict[str, float] | None = None,
    fund_score_boost: float = 0.0,
) -> float | None:
    current = prices.at[current_date, symbol] if symbol in prices.columns else np.nan
    if pd.isna(current) or current <= 0:
        return None

    idx = prices.index.get_loc(current_date)
    if idx < max(variant.lookback_slow, 200):
        return None
    past_fast = prices.iloc[idx - variant.lookback_fast][symbol]
    past_slow = prices.iloc[idx - variant.lookback_slow][symbol]
    past_20 = prices.iloc[idx - 20][symbol]
    qqq_now = prices.at[current_date, "QQQ"]
    qqq_past = prices.iloc[idx - variant.lookback_fast]["QQQ"]
    ma50 = prices.at[current_date, f"{symbol}_MA50"]
    ma200 = prices.at[current_date, f"{symbol}_MA200"]
    if any(pd.isna(x) or x <= 0 for x in [past_fast, past_slow, past_20, qqq_now, qqq_past, ma50, ma200]):
        return None
    if current < ma50 or current < ma200:
        return None

    mom_fast = current / past_fast - 1
    mom_slow = current / past_slow - 1
    mom_20 = current / past_20 - 1
    qqq_mom = qqq_now / qqq_past - 1
    rel_strength = mom_fast - qqq_mom

    if mom_fast <= 0 or mom_slow <= 0:
        return None
    base_score = 0.40 * mom_fast + 0.30 * mom_slow + 0.15 * mom_20 + 0.15 * rel_strength
    if fund_scores and fund_score_boost:
        base_score += fund_score_boost * float(fund_scores.get(symbol, 0.0))
    return base_score


def _target_weights(
    prices: pd.DataFrame,
    current_date: Any,
    candidates: list[str],
    variant: Variant,
    fund_scores: dict[str, float] | None = None,
    fund_score_boost: float = 0.0,
) -> tuple[dict[str, float], list[str], str]:
    row = prices.loc[current_date]
    symbols = [s for s in candidates if s in prices.columns and s not in {"QQQ"}]
    all_weight_keys = sorted(set(symbols + ["QQQ", "SMH"]))
    weights = {symbol: 0.0 for symbol in all_weight_keys}

    if not _risk_on(row):
        weights[variant.risk_off_asset] = min(variant.risk_off_exposure, variant.gross_exposure)
        return weights, [variant.risk_off_asset], "risk_off"

    scored = []
    for symbol in symbols:
        score = _candidate_score(prices, current_date, symbol, variant, fund_scores, fund_score_boost)
        if score is not None:
            scored.append((symbol, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    picks = [symbol for symbol, _ in scored[: variant.top_n]]
    if len(picks) < variant.min_candidates:
        weights[variant.risk_off_asset] = min(variant.risk_off_exposure, variant.gross_exposure)
        return weights, [variant.risk_off_asset], "not_enough_confirmed_candidates"

    fixed = 0.0
    if variant.qqq_floor:
        weights["QQQ"] = variant.qqq_floor
        fixed += variant.qqq_floor
    if variant.smh_floor and "SMH" in prices.columns:
        weights["SMH"] = variant.smh_floor
        fixed += variant.smh_floor
    remaining = max(0.0, variant.gross_exposure - fixed)
    per_position = min(remaining / len(picks), variant.max_position)
    for symbol in picks:
        weights[symbol] = per_position
    return weights, picks, "risk_on"


def _metrics(equity: pd.Series) -> dict[str, float]:
    returns = equity.pct_change().fillna(0.0)
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    total_return = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1
    drawdown = equity / equity.cummax() - 1
    volatility = returns.std() * np.sqrt(252) if len(returns) > 1 else 0.0
    sharpe = returns.mean() * 252 / volatility if volatility else 0.0
    hit_rate = float((returns > 0).mean()) if len(returns) else 0.0
    return {
        "total_return_pct": total_return * 100,
        "cagr_pct": cagr * 100,
        "max_drawdown_pct": drawdown.min() * 100,
        "annual_volatility_pct": volatility * 100,
        "sharpe_no_rf": sharpe,
        "positive_day_rate_pct": hit_rate * 100,
        "final_equity": float(equity.iloc[-1]),
    }


def _run_variant(
    prices: pd.DataFrame,
    candidates: list[str],
    variant: Variant,
    initial_capital: float,
    fund_scores: dict[str, float] | None = None,
    fund_score_boost: float = 0.0,
) -> dict[str, Any]:
    indicator_ready = prices.index[
        prices["QQQ_MA200"].notna()
        & prices["QQQ_MA50"].notna()
    ]
    if len(indicator_ready) < 20:
        raise RuntimeError("Not enough QQQ history after indicator warmup.")
    rebalance_days = _weekly_rebalance_dates(indicator_ready)
    returns = prices.pct_change().fillna(0.0)
    current_weights = {symbol: 0.0 for symbol in prices.columns if not symbol.endswith(("_MA50", "_MA200"))}
    equity = float(initial_capital)
    equity_curve: list[tuple[Any, float]] = []
    state_counts: dict[str, int] = {}
    rebalance_log: list[dict[str, Any]] = []
    turnover = 0.0
    initialized = False

    for current_date in indicator_ready:
        if not initialized:
            target, picks, regime = _target_weights(prices, current_date, candidates, variant, fund_scores, fund_score_boost)
            turnover += sum(abs(target.get(s, 0.0) - current_weights.get(s, 0.0)) for s in set(target) | set(current_weights))
            current_weights = target
            initialized = True
            rebalance_log.append({"date": str(current_date), "regime": regime, "picks": picks, "weights": target})
            equity_curve.append((current_date, equity))
            state_counts[regime] = state_counts.get(regime, 0) + 1
            continue

        day_return = 0.0
        for symbol, weight in current_weights.items():
            if symbol in returns.columns and pd.notna(returns.at[current_date, symbol]):
                day_return += weight * float(returns.at[current_date, symbol])
        equity *= 1 + day_return
        equity_curve.append((current_date, equity))

        if current_date in rebalance_days:
            target, picks, regime = _target_weights(prices, current_date, candidates, variant, fund_scores, fund_score_boost)
            turnover += sum(abs(target.get(s, 0.0) - current_weights.get(s, 0.0)) for s in set(target) | set(current_weights))
            current_weights = target
            rebalance_log.append({"date": str(current_date), "regime": regime, "picks": picks, "weights": target})
            state_counts[regime] = state_counts.get(regime, 0) + 1

    equity = pd.Series(
        data=[value for _, value in equity_curve],
        index=pd.to_datetime([day for day, _ in equity_curve]),
    )
    latest_rebalance = rebalance_log[-1] if rebalance_log else {}
    result = {
        "variant": variant.name,
        "params": variant.__dict__,
        "start": str(equity.index[0].date()),
        "end": str(equity.index[-1].date()),
        "metrics": _metrics(equity),
        "state_counts": state_counts,
        "turnover_weight_sum": turnover,
        "latest_rebalance": latest_rebalance,
        "equity_curve": [{"date": str(idx.date()), "equity": float(value)} for idx, value in equity.items()],
    }
    return result


def _prepare_prices(history: dict[str, list[dict[str, Any]]]) -> pd.DataFrame:
    prices = _price_frame(history)
    base_symbols = [symbol for symbol in prices.columns if not symbol.endswith(("_MA50", "_MA200"))]
    indicators: dict[str, pd.Series] = {}
    for symbol in base_symbols:
        indicators[f"{symbol}_MA50"] = prices[symbol].rolling(50).mean()
        indicators[f"{symbol}_MA200"] = prices[symbol].rolling(200).mean()
    indicator_frame = pd.DataFrame(indicators, index=prices.index)
    return pd.concat([prices, indicator_frame], axis=1)


def _load_fund_tracker_feed(path: Path, max_symbols: int) -> dict[str, Any]:
    if not path.exists():
        return {
            "enabled": False,
            "path": str(path),
            "candidate_symbols": [],
            "normalized_symbol_scores": {},
            "note": "No fund holdings tracker cache found. Run scripts/fund_holdings_tracker.py update first.",
        }
    data = read_json(path)
    feed = data.get("backtest_feed", {})
    symbols = [str(symbol).upper() for symbol in feed.get("candidate_symbols", [])][:max_symbols]
    raw_scores = {
        str(symbol).upper(): float(score)
        for symbol, score in feed.get("normalized_symbol_scores", {}).items()
        if str(symbol).upper() in symbols
    }
    return {
        "enabled": True,
        "path": str(path),
        "timestamp": data.get("timestamp"),
        "successful_source_count": data.get("successful_source_count"),
        "source_count": data.get("source_count"),
        "candidate_symbols": symbols,
        "normalized_symbol_scores": raw_scores,
        "score_method": feed.get("score_method"),
        "note": "Latest public holdings feed loaded. This is an idea-generation overlay unless enough point-in-time snapshots exist.",
    }


def run_backtest_v2(
    start: str,
    end: str,
    initial_capital: float,
    extra_symbols: list[str],
    exclude_symbols: list[str],
    use_fund_tracker: bool = True,
    fund_tracker_path: Path = FUND_HOLDINGS_LATEST_PATH,
    fund_tracker_max_symbols: int = 30,
    fund_score_boost: float = 0.05,
) -> dict[str, Any]:
    rules = read_json(RULE_PATH)
    configured = list(rules.get("universe", []))
    excludes = {symbol.upper() for symbol in exclude_symbols}
    fund_tracker = (
        _load_fund_tracker_feed(fund_tracker_path, fund_tracker_max_symbols)
        if use_fund_tracker
        else {
            "enabled": False,
            "path": str(fund_tracker_path),
            "candidate_symbols": [],
            "normalized_symbol_scores": {},
            "note": "Fund tracker disabled by CLI flag.",
        }
    )
    fund_symbols = list(fund_tracker.get("candidate_symbols", []))
    candidates = sorted(set(V2_CANDIDATES + configured + extra_symbols + fund_symbols) - excludes)
    required = sorted(set(candidates + ["QQQ", "SMH"]))
    history, errors = fetch_daily_history_resilient(required, start=start, end=end)
    prices = _prepare_prices(history)
    usable_candidates = [s for s in candidates if s in prices.columns and s not in {"QQQ"}]
    usable_fund_scores = {
        symbol: float(score)
        for symbol, score in fund_tracker.get("normalized_symbol_scores", {}).items()
        if symbol in usable_candidates
    }
    variant_results = [
        _run_variant(prices, usable_candidates, variant, initial_capital, usable_fund_scores, fund_score_boost)
        for variant in VARIANTS
    ]
    variant_results.sort(key=lambda item: item["metrics"]["cagr_pct"], reverse=True)
    best = variant_results[0]
    validation_flags = [
        "transaction_costs_not_modeled",
        "slippage_not_modeled",
        "latest_fund_feed_not_point_in_time" if fund_tracker.get("enabled") else "",
    ]
    if "130pct" in best.get("variant", ""):
        validation_flags.append("uses_margin_simulation")
    if best["metrics"]["max_drawdown_pct"] <= -25:
        validation_flags.append("max_drawdown_above_paper_threshold")
    validation_flags = [flag for flag in validation_flags if flag]
    validation_status = "production_blocked"
    if best["metrics"]["max_drawdown_pct"] > -20 and not fund_tracker.get("enabled"):
        validation_status = "paper_eligible_review"
    return {
        "task": "backtest_v2",
        "timestamp": now_iso(),
        "requested_start": start,
        "requested_end": end,
        "initial_capital": initial_capital,
        "data_source": "moomoo OpenD historical daily adjusted K-line",
        "configured_universe": configured,
        "candidate_universe": candidates,
        "excluded_symbols": sorted(excludes),
        "usable_candidates": usable_candidates,
        "fund_tracker": {
            **fund_tracker,
            "usable_candidate_symbols": sorted(usable_fund_scores),
            "fund_score_boost": fund_score_boost,
        },
        "data_errors": errors,
        "assumptions": {
            "purpose": "Search for higher-growth strategy variants near a 40% CAGR target.",
            "execution": "research-only; no account query and no orders",
            "rebalance": "weekly, signal at close and next trading day return uses prior weights",
            "transaction_costs": "not modeled",
            "slippage": "not modeled",
            "margin_variant": "v2_top3_130pct_margin_sim uses 130% gross exposure and is suitable only for simulation analysis",
            "fund_tracker_overlay": "If enabled, the latest public holdings tracker expands the candidate universe and adds a small conviction score. Until point-in-time snapshots accumulate, this is not a clean historical signal.",
        },
        "validation_gate": {
            "status": validation_status,
            "flags": validation_flags,
            "policy": "Backtests can inform research and paper review, but cannot create order intents without fresh signals, event checks, and guard approval.",
        },
        "variants": variant_results,
    }


def _pct(value: float) -> str:
    return f"{value:.2f}%"


def write_outputs(result: dict[str, Any]) -> tuple[Path, Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "backtest_v2"
    if result.get("excluded_symbols"):
        safe_excludes = "-".join(symbol.lower() for symbol in result["excluded_symbols"])
        suffix = f"{suffix}_exclude_{safe_excludes}"
    report_path = REPORTS_DIR / f"{today_stamp()}_{suffix}.md"
    json_path = REPORTS_DIR / f"{today_stamp()}_{suffix}.json"
    write_json(json_path, result)

    lines = [
        "# Agentic Investor Backtest V2",
        "",
        f"- Timestamp: {result['timestamp']}",
        f"- Requested period: {result['requested_start']} to {result['requested_end']}",
        f"- Initial capital: ${result['initial_capital']:,.2f}",
        f"- Data source: {result['data_source']}",
        f"- Validation gate: {result.get('validation_gate', {}).get('status', 'research_only')}",
        f"- Usable candidates: {', '.join(result['usable_candidates'])}",
        f"- Fund tracker: {result.get('fund_tracker', {}).get('note', 'n/a')}",
        "",
        "## Variant Leaderboard",
        "",
        "| Variant | CAGR | Total Return | Max DD | Vol | Sharpe | Final Equity | Latest Picks |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for variant in result["variants"]:
        metrics = variant["metrics"]
        latest = variant.get("latest_rebalance", {})
        picks = ", ".join(latest.get("picks", []))
        lines.append(
            "| {name} | {cagr} | {total} | {dd} | {vol} | {sharpe:.2f} | ${final:,.2f} | {picks} |".format(
                name=variant["variant"],
                cagr=_pct(metrics["cagr_pct"]),
                total=_pct(metrics["total_return_pct"]),
                dd=_pct(metrics["max_drawdown_pct"]),
                vol=_pct(metrics["annual_volatility_pct"]),
                sharpe=metrics["sharpe_no_rf"],
                final=metrics["final_equity"],
                picks=picks,
            )
        )

    best = result["variants"][0]
    best_metrics = best["metrics"]
    target_gap = 40.0 - best_metrics["cagr_pct"]
    lines += [
        "",
        "## Best Variant",
        "",
        f"- Name: {best['variant']}",
        f"- CAGR: {_pct(best_metrics['cagr_pct'])}",
        f"- Gap to 40% CAGR target: {_pct(target_gap)}",
        f"- Max drawdown: {_pct(best_metrics['max_drawdown_pct'])}",
        f"- Latest regime: {best.get('latest_rebalance', {}).get('regime', 'n/a')}",
        f"- Latest picks: {', '.join(best.get('latest_rebalance', {}).get('picks', []))}",
        "",
        "## Validation Gate",
        "",
        f"- Status: {result.get('validation_gate', {}).get('status')}",
        f"- Flags: {', '.join(result.get('validation_gate', {}).get('flags', [])) or 'none'}",
        f"- Policy: {result.get('validation_gate', {}).get('policy')}",
        "",
        "## Assumptions",
        "",
    ]
    for key, value in result["assumptions"].items():
        lines.append(f"- {key}: {value}")
    if result["data_errors"]:
        lines += ["", "## Data Gaps", ""]
        for code, error in sorted(result["data_errors"].items()):
            lines.append(f"- {code}: {error}")
    fund_tracker = result.get("fund_tracker", {})
    lines += [
        "",
        "## Fund Holdings Overlay",
        "",
        f"- Enabled: {fund_tracker.get('enabled')}",
        f"- Latest tracker timestamp: {fund_tracker.get('timestamp', 'n/a')}",
        f"- Source success: {fund_tracker.get('successful_source_count', 'n/a')}/{fund_tracker.get('source_count', 'n/a')}",
        f"- Fund score boost: {fund_tracker.get('fund_score_boost')}",
        f"- Feed symbols used: {', '.join(fund_tracker.get('usable_candidate_symbols', [])) or 'none'}",
        f"- Note: {fund_tracker.get('note', 'n/a')}",
    ]
    lines += [
        "",
        "## Interpretation",
        "",
        "- A variant that reaches high CAGR through concentration or 130% gross exposure should be treated as a candidate for paper trading only until drawdown, slippage, and event risk are modeled.",
        "- If no V2 variant approaches 40% CAGR, the target likely requires either a broader alpha universe, explicit option overlay, or accepting materially higher drawdown.",
        "",
        "## Safety",
        "",
        "This V2 backtest is research-only. It did not query accounts, unlock trading, place orders, or modify broker state.",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    log = read_json(TRADE_LOG_PATH, {"records": []})
    log.setdefault("records", []).append(
        {
            "timestamp": result["timestamp"],
            "task": "backtest_v2",
            "status": "completed",
            "summary": f"Best V2 variant {best['variant']}: CAGR {_pct(best_metrics['cagr_pct'])}, max drawdown {_pct(best_metrics['max_drawdown_pct'])}.",
            "report": str(report_path.relative_to(ROOT)),
            "proposals": [],
        }
    )
    write_json(TRADE_LOG_PATH, log)
    return report_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run higher-growth advisory-only backtest variants.")
    parser.add_argument("--start", default=_default_start())
    parser.add_argument("--end", default=_default_end())
    parser.add_argument("--initial-capital", type=float, default=135000.0)
    parser.add_argument("--extra-symbol", action="append", default=[], help="Add a US ticker to the V2 candidate universe.")
    parser.add_argument("--exclude-symbol", action="append", default=[], help="Exclude a US ticker from the V2 candidate universe.")
    parser.add_argument("--no-fund-tracker", action="store_true", help="Disable latest public manager holdings overlay.")
    parser.add_argument("--fund-tracker-path", default=str(FUND_HOLDINGS_LATEST_PATH), help="Path to fund_holdings_tracker latest.json.")
    parser.add_argument("--fund-tracker-max-symbols", type=int, default=30, help="Maximum tracker symbols added to the candidate universe.")
    parser.add_argument("--fund-score-boost", type=float, default=0.05, help="Small score boost applied to tracker-backed symbols. Set 0 for universe-only feed.")
    args = parser.parse_args()
    result = run_backtest_v2(
        args.start,
        args.end,
        args.initial_capital,
        args.extra_symbol,
        args.exclude_symbol,
        use_fund_tracker=not args.no_fund_tracker,
        fund_tracker_path=Path(args.fund_tracker_path),
        fund_tracker_max_symbols=args.fund_tracker_max_symbols,
        fund_score_boost=args.fund_score_boost,
    )
    report, json_path = write_outputs(result)
    print(json.dumps({"status": "completed", "report": str(report), "json": str(json_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
