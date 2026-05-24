from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from agentic_investor_common import (
    REPORTS_DIR,
    ROOT,
    append_trade_log,
    now_iso,
    read_json,
    today_stamp,
    write_json,
)
from guard_pipeline import run_guard_pipeline
from paper_fill_engine import run_paper_fill_engine


TRADING_DIR = ROOT / "data" / "trading"
PLAYBOOK_PATH = TRADING_DIR / "conditional-playbook.json"
STAGED_ORDERS_PATH = TRADING_DIR / "staged-orders.json"
COMMITS_PATH = TRADING_DIR / "commits.jsonl"
EVENT_LOG_PATH = ROOT / "data" / "event-log" / "events.jsonl"
MARKET_SNAPSHOT_PATH = ROOT / "data" / "market" / "latest.json"

EXECUTABLE_SCENARIO_STATUSES = {"APPROVED", "ARMED"}
TERMINAL_SCENARIO_STATUSES = {"FILLED", "TRIGGERED", "EXPIRED", "CANCELLED", "BLOCKED"}


def _default_playbook() -> dict[str, Any]:
    return {
        "version": "1.0",
        "last_updated": now_iso(),
        "policy": {
            "purpose": "Pre-approved conditional paper-trade playbook.",
            "advisory_only": True,
            "broker_execution_enabled": False,
            "account_read_enabled": False,
            "live_order_placement": False,
            "requires_user_approval_before_market_open": True,
        },
        "sessions": [],
    }


def _load_playbook() -> dict[str, Any]:
    data = read_json(PLAYBOOK_PATH, _default_playbook())
    data.setdefault("sessions", [])
    data.setdefault("policy", {})
    data["policy"].update(
        {
            "advisory_only": True,
            "broker_execution_enabled": False,
            "account_read_enabled": False,
            "live_order_placement": False,
            "requires_user_approval_before_market_open": True,
        }
    )
    return data


def _save_playbook(data: dict[str, Any]) -> None:
    data["last_updated"] = now_iso()
    write_json(PLAYBOOK_PATH, data)


def _append_jsonl(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _hash_payload(payload: dict[str, Any], length: int = 12) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]


def _money(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _latest_market_values() -> dict[str, dict[str, Any]]:
    market = read_json(MARKET_SNAPSHOT_PATH, {})
    values: dict[str, dict[str, Any]] = {}
    for section in ("indices", "trueMacroSeries", "macroProxies", "sectorEtfs", "watchSymbols"):
        for row in market.get(section, []):
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            values[symbol] = {**row, "section": section, "timestamp": market.get("timestamp")}
    return values


def _metric_value(condition: dict[str, Any], market_values: dict[str, dict[str, Any]], scenario: dict[str, Any]) -> float | None:
    metric = str(condition.get("metric") or "price").lower()
    symbol = str(condition.get("symbol") or scenario.get("symbol") or "").upper()
    if metric in {"price", "last", "last_price"}:
        return _money(market_values.get(symbol, {}).get("last"))
    if metric in {"day_change_pct", "day"}:
        return _money(market_values.get(symbol, {}).get("dayChangePct"))
    if metric in {"momentum_30d_pct", "momentum30d"}:
        return _money(market_values.get(symbol, {}).get("momentum30dPct"))
    if metric in {"vix", "vix_spot"}:
        return _money(market_values.get("VIX", {}).get("last"))
    if metric in {"dgs10", "10y", "10y_yield"}:
        return _money(market_values.get("DGS10", {}).get("last"))
    if metric in {"usdcnh", "usd_cnh"}:
        return _money(market_values.get("USDCNH", {}).get("last"))
    return None


def _compare(actual: float | None, operator: str, expected: float) -> bool:
    if actual is None:
        return False
    if operator == ">":
        return actual > expected
    if operator == ">=":
        return actual >= expected
    if operator == "<":
        return actual < expected
    if operator == "<=":
        return actual <= expected
    if operator in {"=", "=="}:
        return abs(actual - expected) < 1e-9
    return False


def _conditions_met(scenario: dict[str, Any], market_values: dict[str, dict[str, Any]]) -> tuple[bool, list[dict[str, Any]]]:
    checks = []
    for condition in scenario.get("conditions", []):
        actual = _metric_value(condition, market_values, scenario)
        operator = str(condition.get("operator") or ">=").strip()
        expected = _money(condition.get("value"))
        passed = _compare(actual, operator, expected)
        checks.append(
            {
                "metric": condition.get("metric") or "price",
                "symbol": condition.get("symbol") or scenario.get("symbol"),
                "operator": operator,
                "expected": expected,
                "actual": actual,
                "passed": passed,
            }
        )
    if not checks:
        return False, [{"metric": "conditions", "passed": False, "reason": "No executable conditions."}]
    return all(row["passed"] for row in checks), checks


def _is_expired(scenario: dict[str, Any], now_text: str) -> bool:
    valid_until = scenario.get("valid_until")
    if not valid_until:
        return False
    try:
        return datetime.fromisoformat(now_text) > datetime.fromisoformat(str(valid_until))
    except ValueError:
        return False


def _is_not_yet_valid(scenario: dict[str, Any], now_text: str) -> bool:
    valid_after = scenario.get("valid_after")
    if not valid_after:
        return False
    try:
        return datetime.fromisoformat(now_text) < datetime.fromisoformat(str(valid_after))
    except ValueError:
        return False


def _intent_from_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": scenario.get("symbol"),
        "side": str(scenario.get("side") or "BUY").upper(),
        "instrument_type": str(scenario.get("instrument_type") or "EQUITY").upper(),
        "order_type": str(scenario.get("order_type") or "LIMIT").upper(),
        "quantity": scenario.get("quantity"),
        "notional": scenario.get("notional"),
        "limit_price": scenario.get("limit_price"),
        "target_weight": scenario.get("target_weight"),
        "strategy": scenario.get("strategy") or "preapproved_conditional_playbook",
        "source": "preapproved_conditional_playbook",
        "rationale": scenario.get("rationale") or "",
        "entry_trigger": scenario.get("entry_trigger") or scenario.get("entry_trigger_text") or "",
        "invalidation": scenario.get("invalidation") or "",
        "max_risk_pct": scenario.get("max_risk_pct"),
        "broker_execution_requested": False,
        "scenario_id": scenario.get("id"),
        "preapproved_paper_only": True,
        "contract_symbol": scenario.get("contract_symbol"),
        "option_contract": scenario.get("option_contract"),
    }


def _load_staged() -> dict[str, Any]:
    data = read_json(STAGED_ORDERS_PATH, {"version": "1.0", "orders": []})
    data.setdefault("orders", [])
    return data


def _save_staged(data: dict[str, Any]) -> None:
    data["last_updated"] = now_iso()
    write_json(STAGED_ORDERS_PATH, data)


def _commit_preapproved_intent(scenario: dict[str, Any], checks: list[dict[str, Any]]) -> dict[str, Any]:
    intent = _intent_from_scenario(scenario)
    guard_result = run_guard_pipeline(intent, refresh_market_data=False)
    if guard_result["status"] == "REJECTED":
        return {
            "status": "BLOCKED_BY_GUARD",
            "scenario_id": scenario.get("id"),
            "guard_result": guard_result,
        }

    timestamp = now_iso()
    intent_id = _hash_payload({"timestamp": timestamp, "scenario_id": scenario.get("id"), "intent": guard_result["intent"]}, 10)
    commit_payload = {
        "timestamp": timestamp,
        "intent_id": intent_id,
        "intent": guard_result["intent"],
        "guard_result": guard_result,
        "user_note": f"Pre-approved conditional paper scenario {scenario.get('id')} triggered.",
        "execution_policy": "PREAPPROVED_PAPER_ONLY_NO_BROKER_ORDER",
        "scenario_id": scenario.get("id"),
        "condition_checks": checks,
    }
    commit_hash = _hash_payload(commit_payload, 12)
    record = {
        "intent_id": intent_id,
        "created_at": timestamp,
        "updated_at": timestamp,
        "status": "COMMITTED_PREAPPROVED",
        "intent": guard_result["intent"],
        "guard_result": guard_result,
        "commit_hash": commit_hash,
        "notes": f"Triggered by pre-approved scenario {scenario.get('id')}.",
        "condition_checks": checks,
    }
    staged = _load_staged()
    staged["orders"].append(record)
    _save_staged(staged)
    _append_jsonl(COMMITS_PATH, {"commit_hash": commit_hash, **commit_payload})
    _append_jsonl(
        EVENT_LOG_PATH,
        {
            "timestamp": timestamp,
            "event_type": "conditional.scenario_triggered",
            "scenario_id": scenario.get("id"),
            "intent_id": intent_id,
            "commit_hash": commit_hash,
            "symbol": guard_result["intent"].get("symbol"),
            "guard_status": guard_result["status"],
        },
    )
    append_trade_log(
        {
            "timestamp": timestamp,
            "task": "conditional_playbook",
            "status": "SCENARIO_TRIGGERED",
            "summary": f"Triggered pre-approved paper scenario {scenario.get('id')} for {guard_result['intent'].get('symbol')}.",
            "report": "",
            "proposals": [record],
        }
    )
    return {
        "status": "COMMITTED_PREAPPROVED",
        "scenario_id": scenario.get("id"),
        "intent_id": intent_id,
        "commit_hash": commit_hash,
        "guard_result": guard_result,
    }


def execute_playbook(state: dict[str, Any]) -> dict[str, Any]:
    timestamp = now_iso()
    playbook = _load_playbook()
    market_values = _latest_market_values()
    triggered: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for session in playbook.get("sessions", []):
        if str(session.get("status") or "").upper() not in {"APPROVED", "ACTIVE", "ARMED"}:
            continue
        for scenario in session.get("scenarios", []):
            scenario_status = str(scenario.get("status") or "DRAFT").upper()
            if scenario_status in TERMINAL_SCENARIO_STATUSES:
                continue
            if scenario_status not in EXECUTABLE_SCENARIO_STATUSES:
                skipped.append({"scenario_id": scenario.get("id"), "status": scenario_status, "reason": "Not approved."})
                continue
            if not scenario.get("approval", {}).get("approved"):
                skipped.append({"scenario_id": scenario.get("id"), "status": scenario_status, "reason": "Missing user pre-approval."})
                continue
            if _is_expired(scenario, timestamp):
                scenario["status"] = "EXPIRED"
                skipped.append({"scenario_id": scenario.get("id"), "status": "EXPIRED", "reason": "Scenario valid_until elapsed."})
                continue
            if _is_not_yet_valid(scenario, timestamp):
                skipped.append({"scenario_id": scenario.get("id"), "status": scenario_status, "reason": "Scenario valid_after has not arrived."})
                continue
            met, checks = _conditions_met(scenario, market_values)
            scenario["last_checked_at"] = timestamp
            scenario["last_condition_checks"] = checks
            if not met:
                skipped.append({"scenario_id": scenario.get("id"), "status": scenario_status, "reason": "Conditions not met.", "checks": checks})
                continue

            commit = _commit_preapproved_intent(scenario, checks)
            if commit["status"] == "BLOCKED_BY_GUARD":
                scenario["status"] = "BLOCKED"
                scenario["blocked_at"] = timestamp
                scenario["guard_result"] = commit.get("guard_result")
                blocked.append(commit)
                continue

            scenario["status"] = "TRIGGERED"
            scenario["triggered_at"] = timestamp
            scenario["intent_id"] = commit.get("intent_id")
            scenario["commit_hash"] = commit.get("commit_hash")
            triggered.append(commit)

    _save_playbook(playbook)
    fill_result = run_paper_fill_engine(state) if triggered else None
    if fill_result:
        filled_ids = {fill.get("intent_id") for fill in fill_result.get("new_fills", [])}
        for session in playbook.get("sessions", []):
            for scenario in session.get("scenarios", []):
                if scenario.get("intent_id") in filled_ids:
                    scenario["status"] = "FILLED"
                    scenario["filled_at"] = timestamp
        _save_playbook(playbook)

    result = {
        "task": "conditional_playbook",
        "timestamp": timestamp,
        "status": "TRIGGERED" if triggered else "NO_TRIGGER",
        "triggered": triggered,
        "blocked": blocked,
        "skipped": skipped[:20],
        "paper_fill": fill_result,
        "policy": playbook.get("policy"),
    }
    return result


def write_outputs(result: dict[str, Any]) -> Path:
    report = REPORTS_DIR / f"{today_stamp()}_conditional_playbook.md"
    lines = [
        "# Conditional Paper Playbook",
        "",
        f"- Timestamp: {result.get('timestamp')}",
        f"- Status: {result.get('status')}",
        f"- Triggered: {len(result.get('triggered', []))}",
        f"- Blocked: {len(result.get('blocked', []))}",
        "- Safety: only pre-approved paper scenarios can execute; no broker order or real account access.",
        "",
        "## Triggered",
        "",
    ]
    if result.get("triggered"):
        for row in result["triggered"]:
            lines.append(f"- {row.get('scenario_id')}: intent {row.get('intent_id')} / commit {row.get('commit_hash')}")
    else:
        lines.append("- No scenario triggered.")

    if result.get("blocked"):
        lines += ["", "## Blocked", ""]
        for row in result["blocked"]:
            lines.append(f"- {row.get('scenario_id')}: blocked by guard")

    report.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report


def run_conditional_playbook(state: dict[str, Any]) -> dict[str, Any]:
    result = execute_playbook(state)
    report = write_outputs(result)
    append_trade_log(
        {
            "timestamp": result["timestamp"],
            "task": "conditional_playbook",
            "status": result["status"],
            "summary": f"Conditional playbook checked; triggered {len(result.get('triggered', []))} scenarios.",
            "report": str(report.relative_to(ROOT)),
            "proposals": result.get("triggered", []),
        }
    )
    result["report"] = str(report)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute pre-approved conditional paper playbook.")
    parser.add_argument("command", choices=["execute", "show"], nargs="?", default="execute")
    args = parser.parse_args()
    if args.command == "show":
        print(json.dumps(_load_playbook(), ensure_ascii=False, indent=2))
        return 0
    state = read_json(ROOT / "state.json", {})
    result = run_conditional_playbook(state)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
