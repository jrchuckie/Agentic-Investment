from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentic_investor_common import ROOT, append_trade_log, now_iso, read_json, today_stamp, write_json


DATA_DIR = ROOT / "data" / "research_committee"
REPORTS_DIR = ROOT / "reports"


def _latest_records(task: str, limit: int = 1) -> list[dict[str, Any]]:
    log = read_json(ROOT / "trade-log.json", {"records": []})
    rows = [row for row in log.get("records", []) if row.get("task") == task]
    return rows[-limit:]


def _top_fund_symbols(limit: int = 8) -> list[dict[str, Any]]:
    tracker = read_json(ROOT / "data" / "fund_holdings" / "latest.json", {})
    symbols = tracker.get("backtest_feed", {}).get("candidate_symbols", [])
    scores = tracker.get("backtest_feed", {}).get("normalized_symbol_scores", {})
    return [{"symbol": symbol, "score": scores.get(symbol)} for symbol in symbols[:limit]]


def _staged_orders() -> list[dict[str, Any]]:
    staged = read_json(ROOT / "data" / "trading" / "staged-orders.json", {"orders": []})
    return staged.get("orders", [])


def _social_sentiment() -> dict[str, Any]:
    return read_json(ROOT / "data" / "social_sentiment" / "latest.json", {})


def _decision_label(state: dict[str, Any], orders: list[dict[str, Any]], earnings: dict[str, Any]) -> str:
    if state.get("paused"):
        return "PAUSED"
    if any(str(order.get("status", "")).endswith("REJECTED") for order in orders):
        return "BLOCKED_REVIEW_REQUIRED"
    if orders:
        return "PAPER_REVIEW_READY"
    if earnings.get("blocked_option_symbols"):
        return "WATCH_EQUITY_ONLY"
    return "WATCH"


def build_committee_result() -> dict[str, Any]:
    state = read_json(ROOT / "state.json", {})
    rules = read_json(ROOT / "rule-engine.json", {})
    earnings = read_json(ROOT / "data" / "events" / "earnings_latest.json", {})
    congress = read_json(ROOT / "data" / "congress_trades" / "latest.json", {})
    health = read_json(ROOT / "data" / "health" / "latest.json", {})
    orders = _staged_orders()
    latest_signals = _latest_records("trading_signals", 1)
    signal_proposals = latest_signals[-1].get("proposals", []) if latest_signals else []
    fund_symbols = _top_fund_symbols()
    blocked_options = earnings.get("blocked_option_symbols", [])
    congress_signals = congress.get("signals", [])[:5]
    social = _social_sentiment()
    social_mood = social.get("marketMood", {})
    social_symbols = social.get("symbolSignals", [])[:6]
    social_crowding = social_mood.get("crowdingRisk", "UNKNOWN")
    decision = _decision_label(state, orders, earnings)

    agents = [
        {
            "role": "market_analyst",
            "stance": "constructive" if state.get("market_state") == "BULL" else "neutral",
            "evidence": [
                f"market_state={state.get('market_state')}",
                f"macro_regime={state.get('macro_regime')}",
                f"health_status={health.get('status', 'unknown')}",
            ],
            "conclusion": "Use market state as a permission layer, not as a standalone buy signal.",
        },
        {
            "role": "technical_analyst",
            "stance": "selective_bullish" if signal_proposals else "insufficient_signal",
            "evidence": [f"{item.get('symbol')}: {item.get('reason')}" for item in signal_proposals],
            "conclusion": "Prefer symbols with positive 30D momentum and price above MA50.",
        },
        {
            "role": "fundamental_manager_overlay",
            "stance": "manager_confirmed",
            "evidence": [f"{item['symbol']} score={item.get('score')}" for item in fund_symbols],
            "conclusion": "Use disclosed manager holdings as a conviction overlay only; avoid treating latest holdings as point-in-time backtest truth.",
        },
        {
            "role": "event_risk_analyst",
            "stance": "option_risk_blocked" if blocked_options else "clear",
            "evidence": [f"blocked_options={', '.join(blocked_options) or 'none'}"],
            "conclusion": "Do not open new option exposure for earnings-blackout symbols.",
        },
        {
            "role": "congress_disclosure_analyst",
            "stance": "idea_source_only",
            "evidence": [
                f"{item.get('symbol')} net_score={round(float(item.get('net_score', 0)), 3)} members={','.join(item.get('members', []))}"
                for item in congress_signals
            ],
            "conclusion": "Treat congressional disclosures as lagged idea sources requiring official verification.",
        },
        {
            "role": "social_sentiment_analyst",
            "stance": (
                "crowding_watch"
                if social_crowding in {"HIGH", "MEDIUM"}
                else "sentiment_overlay_ready"
                if social.get("status") == "PASS"
                else "sentiment_data_stale_or_missing"
            ),
            "evidence": [
                f"market_mood={social_mood.get('labelZh', social_mood.get('label', 'missing'))} score={social_mood.get('score', 'n/a')}",
                f"crowding_risk={social_crowding}",
                *[
                    f"{item.get('symbol')}: sentiment={item.get('sentimentLabelZh')} net={item.get('netSentiment')} crowding={item.get('crowdingRisk')}"
                    for item in social_symbols
                ],
            ],
            "conclusion": "Use social media as a narrative/crowding overlay only; it cannot bypass price, macro, event-risk, sizing, or user-approval gates.",
        },
        {
            "role": "bull_researcher",
            "stance": "starter_basket_case" if orders else "watchlist_case",
            "evidence": [f"{order.get('intent', {}).get('symbol')} target={order.get('intent', {}).get('target_weight')}" for order in orders],
            "conclusion": "If zero exposure and gates allow, a small equity-only starter basket is more coherent than staying fully in cash.",
        },
        {
            "role": "bear_researcher",
            "stance": "risk_review_required",
            "evidence": [
                f"macro_regime={state.get('macro_regime')}",
                f"new_options_allowed={False if state.get('macro_regime') in {'HAWKISH', 'UNKNOWN'} else 'unknown'}",
                f"blocked_options={', '.join(blocked_options) or 'none'}",
                f"social_crowding_risk={social_crowding}",
            ],
            "conclusion": "Keep sizing conservative and reject any attempt to bypass macro or earnings gates.",
        },
        {
            "role": "trader",
            "stance": "advisory_only",
            "evidence": [f"{order.get('intent_id')} {order.get('status')} {order.get('intent', {}).get('symbol')}" for order in orders],
            "conclusion": "Translate approved research into staged order intents only; no broker order is placed.",
        },
        {
            "role": "risk_manager",
            "stance": "manual_review",
            "evidence": [
                f"max_single_stock={rules.get('risk_management', {}).get('position_limit', {}).get('max_single_stock')}",
                f"execution_mode={state.get('execution_mode')}",
                f"allow_order_placement={state.get('allow_order_placement')}",
            ],
            "conclusion": "All paper intents remain review-gated until execution mode is explicitly changed.",
        },
        {
            "role": "portfolio_manager",
            "stance": decision,
            "evidence": [
                f"open_intents={len(orders)}",
                f"blocked_options={len(blocked_options)}",
                f"social_mood={social_mood.get('labelZh', social_mood.get('label', 'missing'))}",
                f"social_crowding={social_crowding}",
            ],
            "conclusion": "Approve for paper review only; keep live trading disabled.",
        },
    ]
    return {
        "task": "research_committee",
        "timestamp": now_iso(),
        "framework": {
            "inspiration": "TauricResearch/TradingAgents multi-agent analyst/debate/risk-manager workflow",
            "implementation": "deterministic advisory memo; no autonomous broker execution",
        },
        "decision": decision,
        "agents": agents,
        "staged_order_count": len(orders),
        "blocked_option_symbols": blocked_options,
        "social_sentiment": {
            "status": social.get("status", "MISSING"),
            "marketMood": social_mood,
            "topSymbols": social_symbols,
        },
        "assumptions": {
            "execution": "advisory-only; no account query and no orders",
            "llm_policy": "Use committee roles to structure reasoning, then pass any intent through deterministic guards.",
        },
    }


def write_outputs(result: dict[str, Any]) -> tuple[Path, Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    latest_path = DATA_DIR / "latest.json"
    snapshot_path = DATA_DIR / f"{today_stamp()}_research_committee.json"
    report_path = REPORTS_DIR / f"{today_stamp()}_research_committee.md"
    write_json(latest_path, result)
    write_json(snapshot_path, result)

    lines = [
        "# Agentic Investor Research Committee",
        "",
        f"- Timestamp: {result['timestamp']}",
        f"- Decision: {result['decision']}",
        f"- Framework: {result['framework']['inspiration']}",
        f"- Implementation: {result['framework']['implementation']}",
        "",
        "## Committee",
        "",
        "| Role | Stance | Evidence | Conclusion |",
        "|---|---|---|---|",
    ]
    for agent in result["agents"]:
        evidence = "<br>".join(str(item) for item in agent.get("evidence", []) if item)
        lines.append(f"| {agent['role']} | {agent['stance']} | {evidence or 'n/a'} | {agent['conclusion']} |")
    lines += [
        "",
        "## Safety",
        "",
        "This committee memo is advisory-only. It did not query accounts, unlock trading, or place orders.",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    append_trade_log(
        {
            "timestamp": result["timestamp"],
            "task": "research_committee",
            "status": result["decision"],
            "summary": f"Research committee decision: {result['decision']}.",
            "report": str(report_path.relative_to(ROOT)),
            "proposals": [],
        }
    )
    return report_path, latest_path


def main() -> int:
    result = build_committee_result()
    report, latest = write_outputs(result)
    print(json.dumps({"status": result["decision"], "report": str(report), "latest": str(latest)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
