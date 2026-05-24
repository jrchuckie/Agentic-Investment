from __future__ import annotations

import argparse
import json
import re
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from agentic_investor_common import REPORTS_DIR, ROOT, TRADE_LOG_PATH, now_iso, read_json, today_stamp, write_json


CONFIG_PATH = ROOT / "event-risk.json"
RULE_PATH = ROOT / "rule-engine.json"
WATCHLIST_PATH = ROOT / "watchlist.json"
FUND_FEED_PATH = ROOT / "data" / "fund_holdings" / "latest.json"
DATA_DIR = ROOT / "data" / "events"
LATEST_PATH = DATA_DIR / "earnings_latest.json"


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _fetch_json(url: str, timeout: int = 20) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 agentic-investor-earnings-event-risk/1.0",
            "Accept": "application/json,text/plain,*/*",
            "Origin": "https://www.nasdaq.com",
            "Referer": "https://www.nasdaq.com/",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _load_symbol_universe(max_fund_symbols: int) -> list[str]:
    rules = read_json(RULE_PATH, {})
    watchlist = read_json(WATCHLIST_PATH, {})
    fund_feed = read_json(FUND_FEED_PATH, {})
    symbols = set(rules.get("universe", []))
    for section in ("tier_core", "tier_satellite"):
        symbols.update(rules.get("portfolio_rules", {}).get(section, {}).get("stocks", []))
    for item in watchlist.get("watchlist", []):
        if item.get("normalized_symbol"):
            symbols.add(str(item["normalized_symbol"]).upper())
    symbols.update(fund_feed.get("backtest_feed", {}).get("candidate_symbols", [])[:max_fund_symbols])
    return sorted(symbol for symbol in symbols if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,7}", str(symbol)))


def _normalize_nasdaq_row(row: dict[str, Any], event_date: date) -> dict[str, Any]:
    symbol = _clean(row.get("symbol") or row.get("ticker") or row.get("companySymbol")).upper()
    eps_forecast = _clean(row.get("epsForecast") or row.get("eps_forecast"))
    time = _clean(row.get("time") or row.get("timeZone") or row.get("when"))
    return {
        "symbol": symbol,
        "company": _clean(row.get("name") or row.get("companyName") or row.get("company")),
        "earnings_date": event_date.isoformat(),
        "time": time or "unknown",
        "eps_forecast": eps_forecast,
        "fiscal_quarter": _clean(row.get("fiscalQuarterEnding") or row.get("fiscalQuarter")),
        "source": "nasdaq_earnings_calendar",
        "raw": row,
    }


def _fetch_nasdaq_events(config: dict[str, Any], start: date, lookahead_days: int, timeout: int) -> tuple[list[dict[str, Any]], dict[str, str]]:
    source = next((src for src in config.get("sources", []) if src.get("enabled") and src.get("source_type") == "nasdaq_public_api"), None)
    if not source:
        return [], {"nasdaq_earnings_calendar": "Source disabled or missing."}

    events: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for offset in range(lookahead_days + 1):
        current_date = start + timedelta(days=offset)
        url = source["url_template"].format(date=current_date.isoformat())
        try:
            payload = _fetch_json(url, timeout=timeout)
            rows = payload.get("data", {}).get("rows") or []
            for row in rows:
                event = _normalize_nasdaq_row(row, current_date)
                if event["symbol"]:
                    events.append(event)
        except Exception as exc:
            text = str(exc)
            if "10013" in text:
                text = "Local network permission blocked the public earnings calendar source."
            errors[current_date.isoformat()] = text
    return events, errors


def _manual_events(config: dict[str, Any], start: date, lookahead_days: int) -> list[dict[str, Any]]:
    end = start + timedelta(days=lookahead_days)
    events = []
    for row in config.get("manual_events", []):
        try:
            event_date = datetime.fromisoformat(str(row["earnings_date"])).date()
        except Exception:
            continue
        if start <= event_date <= end:
            events.append({**row, "source": row.get("source", "manual_event")})
    return events


def _risk_level(days_until: int, config: dict[str, Any]) -> str:
    policy = config.get("policy", {})
    if days_until <= int(policy.get("earnings_blackout_days", 2)):
        return "HIGH"
    if days_until <= int(policy.get("earnings_review_days", 7)):
        return "REVIEW"
    return "WATCH"


def _option_playbook(event: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    risk = event["risk_level"]
    for rule in config.get("option_action_rules", []):
        if rule.get("risk_level") == risk:
            return {
                "call_action": rule.get("call_action", ""),
                "put_action": rule.get("put_action", ""),
                "spread_action": rule.get("spread_action", ""),
            }
    return {
        "call_action": "Monitor IV and avoid oversizing long calls into the event.",
        "put_action": "Monitor IV and avoid oversizing long puts into the event.",
        "spread_action": "Use defined-risk structures only if there is an explicit thesis.",
    }


def build_earnings_result(args: argparse.Namespace) -> dict[str, Any]:
    config = read_json(CONFIG_PATH, {})
    start = datetime.fromisoformat(args.start).date() if args.start else date.today()
    lookahead = args.lookahead_days or int(config.get("policy", {}).get("default_lookahead_days", 14))
    universe = set(_load_symbol_universe(args.max_fund_symbols))
    source_events, errors = _fetch_nasdaq_events(config, start, lookahead, args.timeout)
    source_events.extend(_manual_events(config, start, lookahead))

    matched: list[dict[str, Any]] = []
    for event in source_events:
        symbol = str(event.get("symbol", "")).upper()
        if symbol not in universe:
            continue
        event_date = datetime.fromisoformat(str(event["earnings_date"])).date()
        days_until = (event_date - start).days
        enriched = {
            **event,
            "days_until": days_until,
            "risk_level": _risk_level(days_until, config),
        }
        enriched["option_playbook"] = _option_playbook(enriched, config)
        matched.append(enriched)
    matched.sort(key=lambda item: (item["days_until"], item["symbol"]))

    blocked = [event["symbol"] for event in matched if event["risk_level"] == "HIGH"]
    review = [event["symbol"] for event in matched if event["risk_level"] == "REVIEW"]
    return {
        "task": "earnings_event_risk",
        "timestamp": now_iso(),
        "start": start.isoformat(),
        "lookahead_days": lookahead,
        "universe": sorted(universe),
        "event_count": len(source_events),
        "matched_event_count": len(matched),
        "events": matched,
        "blocked_option_symbols": sorted(set(blocked)),
        "review_option_symbols": sorted(set(review)),
        "errors": errors,
        "assumptions": {
            "execution": "advisory-only; no account query and no orders",
            "option_policy": "Earnings events create option risk gates and review notes; they do not place or modify broker orders.",
            "source_warning": "Public earnings calendars can revise dates; verify high-impact events before staging option intents.",
        },
    }


def write_outputs(result: dict[str, Any]) -> tuple[Path, Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    snapshot_path = DATA_DIR / f"{stamp}_earnings_event_risk.json"
    write_json(snapshot_path, result)
    write_json(LATEST_PATH, result)

    report_path = REPORTS_DIR / f"{today_stamp()}_earnings_event_risk.md"
    lines = [
        "# Earnings Event Risk",
        "",
        f"- Timestamp: {result['timestamp']}",
        f"- Window: {result['start']} + {result['lookahead_days']} days",
        f"- Universe symbols: {len(result['universe'])}",
        f"- Matching earnings events: {result['matched_event_count']}",
        f"- Block new option risk: {', '.join(result['blocked_option_symbols']) or 'none'}",
        f"- Manual review option risk: {', '.join(result['review_option_symbols']) or 'none'}",
        "",
        "## Events",
        "",
        "| Symbol | Company | Date | Time | Days | Risk | EPS Forecast | Call/Put Guidance |",
        "|---|---|---:|---|---:|---|---:|---|",
    ]
    for event in result["events"]:
        guidance = "Calls: {call} Puts: {put}".format(
            call=event["option_playbook"].get("call_action", ""),
            put=event["option_playbook"].get("put_action", ""),
        )
        lines.append(
            "| {symbol} | {company} | {date} | {time} | {days} | {risk} | {eps} | {guidance} |".format(
                symbol=event["symbol"],
                company=event.get("company", ""),
                date=event["earnings_date"],
                time=event.get("time", "unknown"),
                days=event["days_until"],
                risk=event["risk_level"],
                eps=event.get("eps_forecast", ""),
                guidance=guidance,
            )
        )
    if result["errors"]:
        lines += ["", "## Source Errors", ""]
        grouped_errors: dict[str, list[str]] = {}
        for key, error in sorted(result["errors"].items()):
            grouped_errors.setdefault(error, []).append(key)
        for error, days in grouped_errors.items():
            preview = ", ".join(days[:5])
            suffix = f" and {len(days) - 5} more" if len(days) > 5 else ""
            lines.append(f"- {error} Dates: {preview}{suffix}.")
    lines += ["", "## Safety", ""]
    for key, value in result["assumptions"].items():
        lines.append(f"- {key}: {value}")
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    log = read_json(TRADE_LOG_PATH, {"records": []})
    log.setdefault("records", []).append(
        {
            "timestamp": result["timestamp"],
            "task": "earnings_event_risk",
            "status": "completed" if not result["errors"] else "completed_with_source_gaps",
            "summary": f"Found {result['matched_event_count']} watched-symbol earnings events in the next {result['lookahead_days']} days.",
            "report": str(report_path.relative_to(ROOT)),
            "proposals": [
                {
                    "type": "option_event_risk",
                    "blocked_option_symbols": result["blocked_option_symbols"],
                    "review_option_symbols": result["review_option_symbols"],
                }
            ],
        }
    )
    write_json(TRADE_LOG_PATH, log)
    return report_path, snapshot_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build daily earnings event-risk and option playbook report.")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD; defaults to today.")
    parser.add_argument("--lookahead-days", type=int, default=None)
    parser.add_argument("--max-fund-symbols", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    result = build_earnings_result(args)
    report, json_path = write_outputs(result)
    print(json.dumps({"status": "completed", "report": str(report), "json": str(json_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
