from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentic_investor_common import ROOT, append_trade_log, now_iso, read_json, today_stamp, write_json


DATA_DIR = ROOT / "data" / "health"
REPORTS_DIR = ROOT / "reports"


JSON_FILES = [
    "state.json",
    "rule-engine.json",
    "watchlist.json",
    "macro-regime.json",
    "event-risk.json",
    "fund-managers.json",
    "congress-traders.json",
    "trade-log.json",
]


def _check_json(path: Path) -> dict[str, Any]:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return {"name": path.name, "status": "PASS", "message": "Valid JSON."}
    except FileNotFoundError:
        return {"name": path.name, "status": "FAIL", "message": "File is missing."}
    except Exception as exc:
        return {"name": path.name, "status": "FAIL", "message": str(exc)}


def _check_moomoo() -> dict[str, Any]:
    try:
        from moomoo_data import fetch_research_snapshot

        snapshot = fetch_research_snapshot(["QQQ"], lookback_days=90)
        records = snapshot.get("records", [])
        if not records:
            return {"status": "WARN", "message": "OpenD connected but returned no QQQ record.", "details": snapshot.get("errors", {})}
        row = records[0]
        return {
            "status": "PASS",
            "message": "OpenD quote/history path is available.",
            "details": {
                "symbol": row.get("symbol"),
                "last_price": row.get("last_price"),
                "momentum_30d_pct": row.get("momentum_30d_pct"),
                "above_ma50": row.get("above_ma50"),
            },
        }
    except Exception as exc:
        return {"status": "WARN", "message": "OpenD quote/history check failed.", "details": {"error": str(exc)}}


def _latest_report(task: str) -> dict[str, Any]:
    files = sorted(REPORTS_DIR.glob(f"*_{task}.md"))
    if not files:
        return {"task": task, "status": "WARN", "message": "No report found."}
    latest = files[-1]
    today = today_stamp()
    status = "PASS" if latest.name.startswith(today) else "WARN"
    return {
        "task": task,
        "status": status,
        "message": f"Latest report: {latest.name}",
        "path": str(latest.relative_to(ROOT)),
    }


def _source_cache(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        return {"name": label, "status": "WARN", "message": "Cache is missing.", "path": str(path.relative_to(ROOT))}
    data = read_json(path, {})
    errors = data.get("errors", {})
    status = "WARN" if errors else "PASS"
    return {
        "name": label,
        "status": status,
        "message": "Cache available." if not errors else f"Cache has {len(errors)} source error(s).",
        "path": str(path.relative_to(ROOT)),
        "timestamp": data.get("timestamp"),
    }


def build_health_result() -> dict[str, Any]:
    staged = read_json(ROOT / "data" / "trading" / "staged-orders.json", {"orders": []})
    json_checks = [_check_json(ROOT / name) for name in JSON_FILES]
    report_checks = [
        _latest_report(task)
        for task in (
            "macro_regime",
            "watchlist_review",
            "earnings_event_risk",
            "trading_signals",
            "order_intents",
        )
    ]
    source_checks = [
        _source_cache(ROOT / "data" / "events" / "earnings_latest.json", "earnings_event_risk"),
        _source_cache(ROOT / "data" / "fund_holdings" / "latest.json", "fund_holdings_tracker"),
        _source_cache(ROOT / "data" / "congress_trades" / "latest.json", "congress_trades_tracker"),
    ]
    moomoo = _check_moomoo()
    all_statuses = [item["status"] for item in json_checks + report_checks + source_checks] + [moomoo["status"]]
    if "FAIL" in all_statuses:
        status = "FAIL"
    elif "WARN" in all_statuses:
        status = "WARN"
    else:
        status = "PASS"
    return {
        "task": "health_check",
        "timestamp": now_iso(),
        "status": status,
        "json_checks": json_checks,
        "moomoo_check": moomoo,
        "report_checks": report_checks,
        "source_checks": source_checks,
        "staged_order_count": len(staged.get("orders", [])),
        "open_review_intents": [
            {
                "intent_id": order.get("intent_id"),
                "status": order.get("status"),
                "symbol": order.get("intent", {}).get("symbol"),
                "guard_status": order.get("guard_result", {}).get("status"),
            }
            for order in staged.get("orders", [])
            if "REJECTED_BY_USER" not in str(order.get("status", ""))
        ],
        "assumptions": {
            "execution": "advisory-only; no account query and no orders",
            "purpose": "Catch silent failures before pre-market decision review.",
        },
    }


def write_outputs(result: dict[str, Any]) -> tuple[Path, Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    latest_path = DATA_DIR / "latest.json"
    snapshot_path = DATA_DIR / f"{today_stamp()}_health_check.json"
    report_path = REPORTS_DIR / f"{today_stamp()}_health_check.md"
    write_json(latest_path, result)
    write_json(snapshot_path, result)

    lines = [
        "# Agentic Investor Health Check",
        "",
        f"- Timestamp: {result['timestamp']}",
        f"- Overall status: {result['status']}",
        f"- Staged order count: {result['staged_order_count']}",
        "",
        "## JSON Files",
        "",
        "| File | Status | Message |",
        "|---|---|---|",
    ]
    for item in result["json_checks"]:
        lines.append(f"| {item['name']} | {item['status']} | {item['message']} |")
    lines += [
        "",
        "## OpenD",
        "",
        f"- Status: {result['moomoo_check']['status']}",
        f"- Message: {result['moomoo_check']['message']}",
        "",
        "## Reports",
        "",
        "| Task | Status | Message |",
        "|---|---|---|",
    ]
    for item in result["report_checks"]:
        lines.append(f"| {item['task']} | {item['status']} | {item['message']} |")
    lines += [
        "",
        "## Source Caches",
        "",
        "| Source | Status | Message | Timestamp |",
        "|---|---|---|---|",
    ]
    for item in result["source_checks"]:
        lines.append(f"| {item['name']} | {item['status']} | {item['message']} | {item.get('timestamp', '')} |")
    lines += [
        "",
        "## Open Review Intents",
        "",
        "| Intent | Status | Symbol | Guard |",
        "|---|---|---|---|",
    ]
    for item in result["open_review_intents"]:
        lines.append(f"| {item.get('intent_id')} | {item.get('status')} | {item.get('symbol')} | {item.get('guard_status')} |")
    if not result["open_review_intents"]:
        lines.append("| none | n/a | n/a | n/a |")
    lines += [
        "",
        "## Safety",
        "",
        "This health check is advisory-only. It did not query accounts, unlock trading, or place orders.",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    append_trade_log(
        {
            "timestamp": result["timestamp"],
            "task": "health_check",
            "status": result["status"],
            "summary": f"Health check completed with status {result['status']}.",
            "report": str(report_path.relative_to(ROOT)),
            "proposals": [],
        }
    )
    return report_path, latest_path


def main() -> int:
    result = build_health_result()
    report, latest = write_outputs(result)
    print(json.dumps({"status": result["status"], "report": str(report), "latest": str(latest)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
