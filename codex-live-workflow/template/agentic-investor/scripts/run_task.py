from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from typing import Any, Callable

from agentic_investor_common import (
    advisory_footer,
    base_report_header,
    current_safety_status,
    finish_task,
    load_context,
    now_iso,
    save_state,
)
from moomoo_data import fetch_research_snapshot


TaskFn = Callable[[dict[str, Any], dict[str, Any]], int]


def _risk_lines(rules: dict[str, Any]) -> list[str]:
    risk = rules.get("risk_management", {})
    drawdown = risk.get("drawdown_control", {})
    position_limit = risk.get("position_limit", {})
    return [
        "## Risk Checks",
        "",
        f"- Daily loss limit: {drawdown.get('daily_loss_limit')}",
        f"- Portfolio max drawdown: {drawdown.get('portfolio_max_drawdown')}",
        f"- Max single stock: {position_limit.get('max_single_stock')}",
        f"- Max option margin usage: {position_limit.get('max_option_margin_usage')}",
        "- Fresh account, quote, and position data are required before any executable proposal.",
        "",
    ]


def _fmt_num(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def pre_market_scan(state: dict[str, Any], rules: dict[str, Any]) -> int:
    lines = base_report_header("pre_market_scan", state, rules)
    lines += [
        "## Checklist",
        "",
        "- Compute NVDA MA50/MA200 and NASDAQ MA50 with fresh market data.",
        "- Resolve market_state before deriving target allocation.",
        "- Review open positions for stop-loss and take-profit triggers.",
        "- Review options expiring in the next 7 calendar days.",
        "",
    ]
    lines += _risk_lines(rules)
    lines += advisory_footer()
    return finish_task(
        "pre_market_scan",
        state,
        "advisory_only",
        "Generated pre-market checklist. No broker call or order placement was attempted.",
        lines,
    )


def trading_signals(state: dict[str, Any], rules: dict[str, Any]) -> int:
    safety = current_safety_status(state)
    proposals: list[dict[str, Any]] = []
    lines = base_report_header("trading_signals", state, rules)
    universe = list(rules.get("universe", []))
    market_proxy = "QQQ"
    scan_symbols = sorted(set(universe + [market_proxy]))

    try:
        research = fetch_research_snapshot(scan_symbols)
    except Exception as exc:
        lines += [
            "## Data Agent",
            "",
            f"- moomoo data pull failed: `{exc}`",
            "- No signal can be generated until OpenD quote access is healthy.",
        ]
        lines += advisory_footer()
        return finish_task(
            "trading_signals",
            state,
            "data_unavailable",
            "moomoo quote pull failed; generated no action.",
            lines,
            proposals,
        )

    records = research["records"]
    by_symbol = {r["symbol"]: r for r in records}
    nvda = by_symbol.get("NVDA", {})
    qqq = by_symbol.get(market_proxy, {})

    market_state = "UNKNOWN"
    if nvda.get("ma50") and qqq.get("ma50"):
        if nvda["last_price"] > nvda["ma50"] and qqq["last_price"] > qqq["ma50"]:
            market_state = "BULL"
        elif nvda.get("ma200") and nvda["last_price"] < nvda["ma200"]:
            market_state = "BEAR"
        elif nvda.get("ma200") and nvda["ma50"] <= nvda["last_price"] <= nvda["ma200"]:
            market_state = "SIDEWAYS"
        else:
            market_state = "SIDEWAYS"
    state["market_state"] = market_state

    def score(row: dict[str, Any]) -> float:
        momentum = row.get("momentum_30d_pct")
        day = row.get("day_change_pct")
        trend = 0
        if row.get("above_ma50"):
            trend += 5
        if row.get("above_ma200"):
            trend += 5
        return (momentum or 0) * 0.7 + (day or 0) * 0.3 + trend

    ranked = sorted([r for r in records if r.get("symbol") in universe], key=score, reverse=True)
    positive = [r for r in ranked if (r.get("momentum_30d_pct") or 0) > 0 and r.get("above_ma50")]
    weak = [
        r for r in ranked
        if (r.get("momentum_30d_pct") is not None and r.get("momentum_30d_pct") < 0)
        or not r.get("above_ma50")
    ]

    for row in positive[:3]:
        proposals.append(
            {
                "type": "watchlist_candidate",
                "symbol": row["symbol"],
                "action": "WATCH",
                "reason": "Positive 30D momentum and price above MA50.",
                "score": round(score(row), 2),
            }
        )

    lines += [
        "## Data Agent",
        "",
        f"- Source: {research['source']} {research['host']}:{research['port']}",
        f"- Lookback: {research['lookback_start']} to {research['lookback_end']}",
        f"- Market proxy: {market_proxy}",
        f"- Derived market state: {market_state}",
        "",
        "| Symbol | Last | Day % | 30D % | MA50 | MA200 | Trend |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in ranked:
        trend = []
        trend.append("above MA50" if row.get("above_ma50") else "below MA50")
        trend.append("above MA200" if row.get("above_ma200") else "below/unknown MA200")
        lines.append(
            "| {symbol} | {last:.2f} | {day} | {mom} | {ma50} | {ma200} | {trend} |".format(
                symbol=row["symbol"],
                last=float(row.get("last_price") or 0),
                day=_fmt_pct(row.get("day_change_pct")),
                mom=_fmt_pct(row.get("momentum_30d_pct")),
                ma50=_fmt_num(row.get("ma50")),
                ma200=_fmt_num(row.get("ma200")),
                trend=", ".join(trend),
            )
        )
    if research["errors"]:
        lines += ["", "### Data Gaps", ""]
        for code, error in research["errors"].items():
            lines.append(f"- {code}: {error}")

    lines += [
        "",
        "## Bull Agent",
        "",
    ]
    if positive:
        lines += [
            "- Strongest watchlist names by momentum/trend: "
            + ", ".join(f"{r['symbol']} ({_fmt_pct(r.get('momentum_30d_pct'))} 30D)" for r in positive[:3])
        ]
    else:
        lines += ["- No configured symbol passed the positive momentum + above MA50 filter."]

    lines += [
        "",
        "## Bear/Risk Agent",
        "",
    ]
    if weak:
        lines += [
            "- Weak or trend-risk names: "
            + ", ".join(f"{r['symbol']} ({_fmt_pct(r.get('momentum_30d_pct'))} 30D)" for r in weak[:5])
        ]
    else:
        lines += ["- No configured symbol was below MA50 or negative on 30D momentum."]

    lines += [
        "",
        "## Investment Committee",
        "",
        "- This automation is advisory-only. It generated watchlist proposals, not orders.",
        "- Missing from this first pass: analyst target upside, portfolio positions, cash, and open orders.",
    ]
    if safety["can_place_simulated_orders"]:
        lines += ["- SIMULATE_AUTO is enabled, but no simulated order was placed by this research task."]
    else:
        lines += ["- Current state permits advisory proposals only."]
    lines += advisory_footer()
    return finish_task(
        "trading_signals",
        state,
        "advisory_watchlist",
        f"Generated moomoo-backed advisory signals for {len(ranked)} symbols.",
        lines,
        proposals,
    )


def mid_day_review(state: dict[str, Any], rules: dict[str, Any]) -> int:
    lines = base_report_header("mid_day_review", state, rules)
    lines += [
        "## Review Items",
        "",
        "- Check submitted/open orders.",
        "- Check intraday P&L against the daily loss limit.",
        "- If the daily loss limit is breached, keep `daily_loss_triggered` true and block new positions.",
    ]
    lines += advisory_footer()
    return finish_task(
        "mid_day_review",
        state,
        "advisory_only",
        "Generated mid-day review checklist.",
        lines,
    )


def post_market_summary(state: dict[str, Any], rules: dict[str, Any]) -> int:
    lines = base_report_header("post_market_summary", state, rules)
    lines += [
        "## Summary Inputs Needed",
        "",
        "- End-of-day total assets, cash, positions, and executed trades.",
        "- Realized and unrealized P&L.",
        "- Open option positions and next expiry dates.",
        "",
        "## Status",
        "",
        "- No account snapshot was pulled by this advisory scaffold.",
    ]
    state["last_daily_report"] = state.get("last_check")
    lines += advisory_footer()
    return finish_task(
        "post_market_summary",
        state,
        "advisory_only",
        "Generated post-market summary template.",
        lines,
    )


def weekly_rebalance(state: dict[str, Any], rules: dict[str, Any]) -> int:
    lines = base_report_header("weekly_rebalance", state, rules)
    split = rules.get("portfolio_rules", {}).get("stock_bucket_split", {})
    lines += [
        "## Rebalance Policy",
        "",
        f"- Core share of stock bucket: {split.get('core')}",
        f"- Satellite share of stock bucket: {split.get('satellite')}",
        f"- Rebalance threshold: {rules.get('portfolio_rules', {}).get('rebalancing', {}).get('trigger_weight_deviation')}",
        "- Generate proposals only; require user confirmation for all real-account changes.",
    ]
    state["last_weekly_report"] = state.get("last_check")
    lines += advisory_footer()
    return finish_task(
        "weekly_rebalance",
        state,
        "advisory_only",
        "Generated weekly rebalance checklist.",
        lines,
    )


def monthly_review(state: dict[str, Any], rules: dict[str, Any]) -> int:
    lines = base_report_header("monthly_review", state, rules)
    lines += [
        "## Monthly Review",
        "",
        f"- Planned monthly DCA: {rules.get('monthly_dca')}",
        f"- Target amount: {rules.get('target_amount')} {rules.get('base_currency')}",
        f"- Target date: {rules.get('target_date')}",
        "- Review capital injection assumptions before using them in allocation sizing.",
    ]
    lines += advisory_footer()
    return finish_task(
        "monthly_review",
        state,
        "advisory_only",
        "Generated monthly review checklist.",
        lines,
    )


def fund_holdings_tracker(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from fund_holdings_tracker import build_tracker_result, write_outputs

    args = SimpleNamespace(
        source=[],
        timeout=None,
        max_backtest_symbols=None,
        allow_stale=True,
    )
    result = build_tracker_result(args)
    report, latest = write_outputs(result)
    state["last_fund_holdings_tracker"] = result.get("timestamp")
    save_state(state)
    print(json.dumps({
        "task": "fund_holdings_tracker",
        "status": "completed",
        "report": str(report),
        "latest": str(latest),
    }, ensure_ascii=False, indent=2))
    return 0


def earnings_event_risk(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from earnings_event_risk import build_earnings_result, write_outputs

    result = build_earnings_result(SimpleNamespace(
        start=None,
        lookahead_days=None,
        max_fund_symbols=30,
        timeout=20,
    ))
    report, json_path = write_outputs(result)
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "earnings_event_risk"
    state["last_earnings_event_risk"] = timestamp
    save_state(state)
    print(json.dumps({
        "task": "earnings_event_risk",
        "status": "completed",
        "report": str(report),
        "json": str(json_path),
        "matched_event_count": result.get("matched_event_count"),
        "blocked_option_symbols": result.get("blocked_option_symbols", []),
        "review_option_symbols": result.get("review_option_symbols", []),
    }, ensure_ascii=False, indent=2))
    return 0


def congress_trades_tracker(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from congress_trades_tracker import build_congress_result, write_outputs

    result = build_congress_result(SimpleNamespace(
        lookback_days=None,
        fetch=False,
        timeout=20,
    ))
    report, json_path = write_outputs(result)
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "congress_trades_tracker"
    state["last_congress_trades_tracker"] = timestamp
    save_state(state)
    print(json.dumps({
        "task": "congress_trades_tracker",
        "status": "completed",
        "report": str(report),
        "json": str(json_path),
        "signals": len(result.get("signals", [])),
    }, ensure_ascii=False, indent=2))
    return 0


def health_check(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from health_check import build_health_result, write_outputs

    result = build_health_result()
    report, latest = write_outputs(result)
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "health_check"
    state["last_health_check"] = timestamp
    state["health_status"] = result.get("status")
    save_state(state)
    print(json.dumps({
        "task": "health_check",
        "status": result.get("status"),
        "report": str(report),
        "latest": str(latest),
        "staged_order_count": result.get("staged_order_count"),
    }, ensure_ascii=False, indent=2))
    return 0


def research_committee(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from research_committee import build_committee_result, write_outputs

    result = build_committee_result()
    report, latest = write_outputs(result)
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "research_committee"
    state["last_research_committee"] = timestamp
    state["research_committee_decision"] = result.get("decision")
    save_state(state)
    print(json.dumps({
        "task": "research_committee",
        "status": result.get("decision"),
        "report": str(report),
        "latest": str(latest),
        "staged_order_count": result.get("staged_order_count"),
    }, ensure_ascii=False, indent=2))
    return 0


def market_snapshot(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from market_snapshot import build_market_snapshot, write_outputs

    result = build_market_snapshot()
    report, latest = write_outputs(result)
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "market_snapshot"
    state["last_market_snapshot"] = timestamp
    state["market_snapshot_status"] = result.get("status")
    save_state(state)
    print(json.dumps({
        "task": "market_snapshot",
        "status": result.get("status"),
        "report": str(report),
        "latest": str(latest),
    }, ensure_ascii=False, indent=2))
    return 0


def valuation_snapshot(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from valuation_snapshot import build_valuation_snapshot, write_outputs

    result = build_valuation_snapshot()
    report, latest = write_outputs(result)
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "valuation_snapshot"
    state["last_valuation_snapshot"] = timestamp
    state["valuation_snapshot_status"] = result.get("status")
    state["valuation_symbols_with_data"] = result.get("symbolsWithData")
    save_state(state)
    print(json.dumps({
        "task": "valuation_snapshot",
        "status": result.get("status"),
        "symbolsWithData": result.get("symbolsWithData"),
        "report": str(report),
        "latest": str(latest),
    }, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"PASS", "WARN"} else 1


def options_snapshot(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from options_snapshot import build_options_snapshot, write_outputs

    result = build_options_snapshot()
    report, latest = write_outputs(result)
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "options_snapshot"
    state["last_options_snapshot"] = timestamp
    state["options_snapshot_status"] = result.get("status")
    state["options_symbols_with_chains"] = result.get("symbolsWithChains")
    save_state(state)
    print(json.dumps({
        "task": "options_snapshot",
        "status": result.get("status"),
        "symbolsWithChains": result.get("symbolsWithChains"),
        "report": str(report),
        "latest": str(latest),
    }, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"PASS", "WARN"} else 1


def paper_fill_engine(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from paper_fill_engine import run_paper_fill_engine

    result = run_paper_fill_engine(state)
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "paper_fill_engine"
    state["last_paper_fill_engine"] = timestamp
    state["paper_portfolio_nav"] = result.get("portfolio", {}).get("nav")
    state["paper_unrealized_pnl"] = result.get("portfolio", {}).get("unrealized_pnl")
    state["total_unrealized_pnl"] = result.get("portfolio", {}).get("unrealized_pnl", state.get("total_unrealized_pnl"))
    save_state(state)
    print(json.dumps({
        "task": "paper_fill_engine",
        "status": result.get("status"),
        "report": result.get("report"),
        "portfolio": result.get("portfolio_path"),
        "new_fill_count": result.get("new_fill_count"),
    }, ensure_ascii=False, indent=2))
    return 0


def openbb_smoke(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from openbb_smoke import build_openbb_smoke, write_outputs

    result = build_openbb_smoke()
    report, snapshot = write_outputs(result)
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "openbb_smoke"
    state["last_openbb_smoke"] = timestamp
    state["openbb_available"] = bool(result.get("diagnostics", {}).get("available"))
    state["openbb_smoke_status"] = result.get("status")
    save_state(state)
    print(json.dumps({
        "task": "openbb_smoke",
        "status": result.get("status"),
        "openbb_available": result.get("diagnostics", {}).get("available"),
        "report": str(report),
        "snapshot": str(snapshot),
    }, ensure_ascii=False, indent=2))
    return 0


def conditional_playbook(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from conditional_playbook import run_conditional_playbook

    result = run_conditional_playbook(state)
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "conditional_playbook"
    state["last_conditional_playbook"] = timestamp
    state["conditional_playbook_status"] = result.get("status")
    save_state(state)
    print(json.dumps({
        "task": "conditional_playbook",
        "status": result.get("status"),
        "triggered": len(result.get("triggered", [])),
        "blocked": len(result.get("blocked", [])),
        "report": result.get("report"),
    }, ensure_ascii=False, indent=2))
    return 0


def watchlist_review(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from watchlist_manager import run_watchlist_review

    report, rows = run_watchlist_review()
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "watchlist_review"
    state["last_watchlist_review"] = timestamp
    save_state(state)
    print(json.dumps({
        "task": "watchlist_review",
        "status": "completed",
        "report": report,
        "reviewed": len(rows),
    }, ensure_ascii=False, indent=2))
    return 0


def macro_regime(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from macro_regime import run_macro_review

    report, summary = run_macro_review()
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "macro_regime"
    state["macro_regime"] = summary.get("regime")
    state["last_macro_regime_review"] = timestamp
    save_state(state)
    print(json.dumps({
        "task": "macro_regime",
        "status": "completed",
        "report": report,
        **summary,
    }, ensure_ascii=False, indent=2))
    return 0


def order_intents(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from order_intent import export_review

    result = export_review(SimpleNamespace())
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "order_intents"
    state["last_order_intents_review"] = timestamp
    save_state(state)
    print(json.dumps({
        "task": "order_intents",
        **result,
    }, ensure_ascii=False, indent=2))
    return 0


def intel_monitor(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from intel_monitor import build_intel_result, write_outputs

    result = build_intel_result(SimpleNamespace(
        fetch=True,
        fetch_x_kols=True,
        timeout=20,
        x_timeout=90,
        max_items_per_source=15,
        max_x_tweets_per_kol=5,
        max_total_items=80,
        max_highlights=12,
        manual_title="",
        manual_url="",
        manual_text="",
        manual_source="",
    ))
    report, json_path = write_outputs(result)
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "intel_monitor"
    state["last_intel_monitor"] = timestamp
    save_state(state)
    print(json.dumps({
        "task": "intel_monitor",
        "status": "completed",
        "report": str(report),
        "json": str(json_path),
        "highlights": len(result.get("highlights", [])),
    }, ensure_ascii=False, indent=2))
    return 0


def social_sentiment_feed(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from social_sentiment_feed import build_social_sentiment_result, write_outputs

    result = build_social_sentiment_result()
    report, latest, snapshot = write_outputs(result)
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "social_sentiment_feed"
    state["last_social_sentiment_feed"] = timestamp
    state["social_sentiment_status"] = result.get("status")
    state["social_sentiment_crowding"] = result.get("marketMood", {}).get("crowdingRisk")
    save_state(state)
    print(json.dumps({
        "task": "social_sentiment_feed",
        "status": result.get("status"),
        "marketMood": result.get("marketMood", {}),
        "report": str(report),
        "latest": str(latest),
        "snapshot": str(snapshot),
    }, ensure_ascii=False, indent=2))
    return 0


def review_dashboard(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from review_dashboard import build_dashboard

    path, _ = build_dashboard("weekly")
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "review_dashboard"
    state["last_review_dashboard"] = timestamp
    save_state(state)
    print(json.dumps({
        "task": "review_dashboard",
        "status": "completed",
        "html": str(path),
    }, ensure_ascii=False, indent=2))
    return 0


def dashboard_snapshot(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from dashboard_snapshot import write_snapshot

    path = write_snapshot()
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "dashboard_snapshot"
    state["last_dashboard_snapshot"] = timestamp
    save_state(state)
    print(json.dumps({
        "task": "dashboard_snapshot",
        "status": "completed",
        "snapshot": str(path),
    }, ensure_ascii=False, indent=2))
    return 0


def strategy_compare_backtest(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from strategy_compare_backtest import build_strategy_compare_backtest, write_outputs

    result = build_strategy_compare_backtest()
    report, latest = write_outputs(result)
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "strategy_compare_backtest"
    state["last_strategy_compare_backtest"] = timestamp
    save_state(state)
    print(json.dumps({
        "task": "strategy_compare_backtest",
        "status": result.get("status"),
        "moomooUsed": result.get("dataPolicy", {}).get("moomooUsed"),
        "strategies": len(result.get("strategies", [])),
        "report": str(report),
        "latest": str(latest),
        "topStrategy": (result.get("ranking") or [{}])[0].get("name"),
    }, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "PASS" else 1


def intraday_monitor(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from conditional_playbook import run_conditional_playbook
    from dashboard_snapshot import write_snapshot
    from intel_monitor import build_intel_result, write_outputs as write_intel_outputs
    from market_snapshot import build_market_snapshot, write_outputs as write_market_outputs
    from order_intent import export_review
    from options_snapshot import build_options_snapshot, write_outputs as write_options_outputs
    from paper_fill_engine import run_paper_fill_engine
    from publish_dashboard_firestore import _credential_path, publish_snapshot
    from research_committee import build_committee_result, write_outputs as write_committee_outputs
    from social_sentiment_feed import build_social_sentiment_result, write_outputs as write_social_outputs
    from valuation_snapshot import build_valuation_snapshot, write_outputs as write_valuation_outputs
    from moomoo_real_account import fetch_real_account_snapshot, write_outputs as write_real_account_outputs

    market_result = build_market_snapshot()
    market_report, market_latest = write_market_outputs(market_result)
    real_account_status: dict[str, Any] = {"status": "skipped"}
    real_account_enabled = os.environ.get("MOOMOO_REAL_ACCOUNT_READ", "").strip() == "1"
    if real_account_enabled:
        try:
            real_snapshot = fetch_real_account_snapshot(
                host=os.environ.get("MOOMOO_OPEND_HOST", "127.0.0.1").strip() or "127.0.0.1",
                port=int(os.environ.get("MOOMOO_OPEND_PORT", "11111").strip() or "11111"),
                security_firm=os.environ.get("MOOMOO_SECURITY_FIRM", "FUTUINC").strip() or "FUTUINC",
                expected_trdmarket=os.environ.get("MOOMOO_EXPECTED_TRDMARKET", "US").strip() or "US",
            )
            real_report, real_latest = write_real_account_outputs(real_snapshot)
            real_account_status = {
                "status": real_snapshot.get("status"),
                "selectedAccount": (real_snapshot.get("selectedAccount") or {}).get("acc_id"),
                "positions": len(((real_snapshot.get("positions") or {}).get("records") or [])),
                "openOrders": len(((real_snapshot.get("orders") or {}).get("records") or [])),
                "latest": str(real_latest),
                "report": str(real_report),
                "note": real_snapshot.get("note"),
            }
        except Exception as exc:
            real_account_status = {"status": "failed", "error": str(exc)}
    valuation_result: dict[str, Any] = {"status": "skipped"}
    valuation_status: dict[str, Any] = {"status": "skipped"}
    try:
        valuation_result = build_valuation_snapshot()
        valuation_report, valuation_latest = write_valuation_outputs(valuation_result)
        valuation_status = {
            "status": valuation_result.get("status"),
            "symbols_with_data": valuation_result.get("symbolsWithData"),
            "latest": str(valuation_latest),
            "report": str(valuation_report),
        }
    except Exception as exc:
        valuation_result = {"status": "failed", "error": str(exc)}
        valuation_status = {"status": "failed", "error": str(exc)}
    options_status: dict[str, Any] = {"status": "skipped"}
    try:
        options_result = build_options_snapshot()
        options_report, options_latest = write_options_outputs(options_result)
        options_status = {
            "status": options_result.get("status"),
            "symbols_with_chains": options_result.get("symbolsWithChains"),
            "latest": str(options_latest),
            "report": str(options_report),
        }
    except Exception as exc:
        options_status = {"status": "failed", "error": str(exc)}
    playbook_result = run_conditional_playbook(state)
    fill_result = run_paper_fill_engine(state)
    intel_status: dict[str, Any] = {"status": "skipped"}
    social_status: dict[str, Any] = {"status": "skipped"}
    try:
        intel_result = build_intel_result(SimpleNamespace(
            fetch=True,
            fetch_x_kols=False,
            manual_title="",
            manual_url="",
            manual_text="",
            manual_source="",
            timeout=20,
            x_timeout=90,
            max_items_per_source=15,
            max_x_tweets_per_kol=0,
            max_total_items=80,
            max_highlights=12,
        ))
        has_fresh_intel = bool(intel_result.get("fetched_item_count") or intel_result.get("highlights") or intel_result.get("event_radar"))
        if not has_fresh_intel:
            raise RuntimeError("intel_monitor returned no fetched items; keeping previous event radar snapshot")
        write_intel_outputs(intel_result)
        intel_status = {
            "status": "completed",
            "highlights": len(intel_result.get("highlights", []) or []),
            "event_radar": len(intel_result.get("event_radar", []) or []),
        }
        social_result = build_social_sentiment_result()
        write_social_outputs(social_result)
        social_status = {
            "status": social_result.get("status"),
            "event_radar": len(social_result.get("eventRadar", []) or []),
            "crowding": social_result.get("marketMood", {}).get("crowdingRisk"),
        }
    except Exception as exc:
        intel_status = {"status": "failed", "error": str(exc)}
        social_status = {"status": "skipped_after_intel_failure"}
    committee_status: dict[str, Any] = {"status": "skipped"}
    try:
        committee_result = build_committee_result()
        committee_report, committee_latest = write_committee_outputs(committee_result)
        committee_status = {
            "status": committee_result.get("decision"),
            "latest": str(committee_latest),
            "report": str(committee_report),
        }
    except Exception as exc:
        committee_status = {"status": "failed", "error": str(exc)}
    order_review = export_review(SimpleNamespace())
    dashboard_path = write_snapshot()
    firebase_result = {
        "status": "skipped_missing_config",
        "message": "FIREBASE_USER_UID and service account credentials are not configured.",
    }
    if os.environ.get("FIREBASE_USER_UID", "").strip() and _credential_path():
        try:
            firebase_result = publish_snapshot()
        except Exception as exc:
            firebase_result = {"status": "publish_failed", "error": str(exc)}

    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "intraday_monitor"
    state["last_intraday_monitor"] = timestamp
    state["last_market_snapshot"] = timestamp
    state["market_snapshot_status"] = market_result.get("status")
    state["last_valuation_snapshot"] = timestamp
    state["valuation_snapshot_status"] = valuation_result.get("status")
    state["valuation_symbols_with_data"] = valuation_result.get("symbolsWithData")
    state["last_options_snapshot"] = timestamp if options_status.get("status") in {"PASS", "WARN"} else state.get("last_options_snapshot")
    state["options_snapshot_status"] = options_status.get("status")
    state["options_symbols_with_chains"] = options_status.get("symbols_with_chains")
    state["last_intel_monitor"] = timestamp if intel_status.get("status") == "completed" else state.get("last_intel_monitor")
    state["last_social_sentiment_feed"] = timestamp if social_status.get("status") in {"PASS", "WARN"} else state.get("last_social_sentiment_feed")
    state["latest_event_radar_count"] = social_status.get("event_radar", intel_status.get("event_radar"))
    state["last_research_committee"] = timestamp if committee_status.get("status") not in {"failed", "skipped"} else state.get("last_research_committee")
    state["research_committee_decision"] = committee_status.get("status")
    state["last_paper_fill_engine"] = timestamp
    state["paper_portfolio_nav"] = fill_result.get("portfolio", {}).get("nav")
    state["paper_unrealized_pnl"] = fill_result.get("portfolio", {}).get("unrealized_pnl")
    state["total_unrealized_pnl"] = fill_result.get("portfolio", {}).get("unrealized_pnl", state.get("total_unrealized_pnl"))
    state["last_dashboard_snapshot"] = timestamp
    state["firebase_publish_status"] = firebase_result.get("status")
    state["real_account_read_enabled"] = bool(real_account_enabled)
    if real_account_enabled:
        state["last_moomoo_real_account_snapshot"] = timestamp
        state["moomoo_real_account_snapshot_status"] = real_account_status.get("status")
    if firebase_result.get("status") == "published":
        state["last_firebase_publish_snapshot"] = timestamp
    save_state(state)

    print(json.dumps({
        "task": "intraday_monitor",
        "status": "completed",
        "market_status": market_result.get("status"),
        "openbb_available": market_result.get("dataSourceDiagnostics", {}).get("openbb", {}).get("available"),
        "openbb_equity_records": market_result.get("dataSourceDiagnostics", {}).get("openbb_equity", {}).get("records"),
        "openbb_macro_records": market_result.get("dataSourceDiagnostics", {}).get("openbb_macro", {}).get("records"),
        "valuation_snapshot": valuation_status,
        "options_snapshot": options_status,
        "conditional_playbook_status": playbook_result.get("status"),
        "triggered": len(playbook_result.get("triggered", [])),
        "new_fill_count": fill_result.get("new_fill_count"),
        "intel_monitor": intel_status,
        "social_sentiment": social_status,
        "research_committee": committee_status,
        "market_report": str(market_report),
        "market_latest": str(market_latest),
        "orders_report": order_review.get("report"),
        "dashboard_snapshot": str(dashboard_path),
        "firebase_publish_status": firebase_result.get("status"),
        "firebase_current_path": firebase_result.get("currentPath"),
        "real_account_snapshot": real_account_status,
        "policy": {
            "advisory_only": True,
            "broker_execution_enabled": False,
            "real_account_read_enabled": bool(real_account_enabled),
        },
    }, ensure_ascii=False, indent=2))
    return 0


def firebase_publish_snapshot(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from publish_dashboard_firestore import publish_snapshot

    result = publish_snapshot()
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "firebase_publish_snapshot"
    state["last_firebase_publish_snapshot"] = timestamp
    state["firebase_publish_status"] = result.get("status")
    save_state(state)
    print(json.dumps({
        "task": "firebase_publish_snapshot",
        **result,
    }, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"published", "skipped_missing_config"} else 1


def firebase_deploy_rules(state: dict[str, Any], rules: dict[str, Any]) -> int:
    from deploy_firestore_rules import deploy_firestore_rules

    result = deploy_firestore_rules()
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = "firebase_deploy_rules"
    state["last_firebase_deploy_rules"] = timestamp
    state["firebase_rules_status"] = result.get("status")
    save_state(state)
    print(json.dumps({
        "task": "firebase_deploy_rules",
        **result,
    }, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "deployed" else 1


def emergency_stop(state: dict[str, Any], rules: dict[str, Any]) -> int:
    state["paused"] = True
    state["allow_order_placement"] = False
    state["execution_mode"] = "ADVISORY_ONLY"
    save_state(state)
    lines = base_report_header("emergency_stop", state, rules)
    lines += [
        "## Emergency Stop",
        "",
        "- Set `paused` to true.",
        "- Set `allow_order_placement` to false.",
        "- Set `execution_mode` to `ADVISORY_ONLY`.",
        "- No broker cancel call was attempted by this scaffold.",
        "- Manually verify open orders in moomoo/OpenD if any broker connection exists.",
    ]
    lines += advisory_footer()
    return finish_task(
        "emergency_stop",
        state,
        "paused",
        "Paused strategy state and disabled order placement.",
        lines,
    )


TASKS: dict[str, TaskFn] = {
    "pre_market_scan": pre_market_scan,
    "trading_signals": trading_signals,
    "mid_day_review": mid_day_review,
    "post_market_summary": post_market_summary,
    "weekly_rebalance": weekly_rebalance,
    "monthly_review": monthly_review,
    "fund_holdings_tracker": fund_holdings_tracker,
    "earnings_event_risk": earnings_event_risk,
    "congress_trades_tracker": congress_trades_tracker,
    "health_check": health_check,
    "research_committee": research_committee,
    "market_snapshot": market_snapshot,
    "valuation_snapshot": valuation_snapshot,
    "options_snapshot": options_snapshot,
    "paper_fill_engine": paper_fill_engine,
    "openbb_smoke": openbb_smoke,
    "conditional_playbook": conditional_playbook,
    "watchlist_review": watchlist_review,
    "macro_regime": macro_regime,
    "order_intents": order_intents,
    "intel_monitor": intel_monitor,
    "social_sentiment_feed": social_sentiment_feed,
    "review_dashboard": review_dashboard,
    "dashboard_snapshot": dashboard_snapshot,
    "strategy_compare_backtest": strategy_compare_backtest,
    "intraday_monitor": intraday_monitor,
    "firebase_publish_snapshot": firebase_publish_snapshot,
    "firebase_deploy_rules": firebase_deploy_rules,
    "emergency_stop": emergency_stop,
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in TASKS:
        print("Usage: python scripts/run_task.py <task>")
        print("Tasks: " + ", ".join(sorted(TASKS)))
        return 2

    task = args[0]
    state, rules = load_context()
    if state.get("paused") and task not in {"emergency_stop", "post_market_summary"}:
        lines = base_report_header(task, state, rules)
        lines += ["## Skipped", "", "Strategy is paused. No task logic was executed."]
        lines += advisory_footer()
        return finish_task(
            task,
            state,
            "skipped_paused",
            "Strategy is paused.",
            lines,
        )

    return TASKS[task](state, rules)


if __name__ == "__main__":
    raise SystemExit(main())
