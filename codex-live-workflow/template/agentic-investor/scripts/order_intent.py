from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from agentic_investor_common import ROOT, append_trade_log, now_iso, read_json, today_stamp, write_json
from guard_pipeline import run_guard_pipeline


TRADING_DIR = ROOT / "data" / "trading"
STAGED_PATH = TRADING_DIR / "staged-orders.json"
COMMITS_PATH = TRADING_DIR / "commits.jsonl"
EVENT_LOG_PATH = ROOT / "data" / "event-log" / "events.jsonl"


def _default_staged() -> dict[str, Any]:
    return {
        "version": "1.0",
        "last_updated": now_iso(),
        "policy": {
            "purpose": "Advisory-only staged order intents. These are not broker orders.",
            "broker_execution_enabled": False,
        },
        "orders": [],
    }


def _load_staged() -> dict[str, Any]:
    data = read_json(STAGED_PATH, _default_staged())
    data.setdefault("orders", [])
    return data


def _save_staged(data: dict[str, Any]) -> None:
    data["last_updated"] = now_iso()
    write_json(STAGED_PATH, data)


def _append_jsonl(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _hash_payload(payload: dict[str, Any], length: int = 12) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]


def _stage_status(guard_result: dict[str, Any]) -> str:
    status = guard_result.get("status")
    if status == "PASSED":
        return "STAGED_READY"
    if status == "REQUIRES_REVIEW":
        return "STAGED_REQUIRES_REVIEW"
    return "STAGED_REJECTED"


def _find_order(data: dict[str, Any], intent_id: str) -> dict[str, Any] | None:
    for order in data.get("orders", []):
        if order.get("intent_id") == intent_id:
            return order
    return None


def build_intent(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "symbol": args.symbol,
        "side": args.side.upper(),
        "instrument_type": args.instrument_type.upper(),
        "order_type": args.order_type.upper(),
        "quantity": args.quantity,
        "notional": args.notional,
        "limit_price": args.limit_price,
        "target_weight": args.target_weight,
        "strategy": args.strategy,
        "source": args.source,
        "rationale": args.rationale,
        "entry_trigger": args.entry_trigger,
        "invalidation": args.invalidation,
        "max_risk_pct": args.max_risk_pct,
        "broker_execution_requested": False,
    }


def stage_intent(args: argparse.Namespace) -> dict[str, Any]:
    intent = build_intent(args)
    guard_result = run_guard_pipeline(intent, refresh_market_data=not args.no_market_data)
    record = {
        "intent_id": _hash_payload({"timestamp": now_iso(), "intent": guard_result["intent"]}, 10),
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "status": _stage_status(guard_result),
        "intent": guard_result["intent"],
        "guard_result": guard_result,
        "notes": args.note or "",
    }
    if args.dry_run:
        return {"status": "dry_run", "record": record}

    data = _load_staged()
    data["orders"].append(record)
    _save_staged(data)
    _append_jsonl(
        EVENT_LOG_PATH,
        {
            "timestamp": now_iso(),
            "event_type": "order.staged",
            "intent_id": record["intent_id"],
            "status": record["status"],
            "symbol": record["intent"]["symbol"],
            "guard_status": guard_result["status"],
        },
    )
    append_trade_log(
        {
            "timestamp": record["created_at"],
            "task": "order_intent_stage",
            "status": record["status"],
            "summary": f"Staged advisory {record['intent']['side']} intent for {record['intent']['symbol']}.",
            "report": "",
            "proposals": [record],
        }
    )
    return {"status": record["status"], "intent_id": record["intent_id"], "guard_status": guard_result["status"]}


def list_intents(args: argparse.Namespace) -> dict[str, Any]:
    data = _load_staged()
    orders = data.get("orders", [])
    if not args.all:
        orders = [order for order in orders if not str(order.get("status", "")).startswith(("COMMITTED", "REJECTED_BY_USER"))]
    return {
        "status": "completed",
        "orders": [
            {
                "intent_id": order.get("intent_id"),
                "status": order.get("status"),
                "symbol": order.get("intent", {}).get("symbol"),
                "side": order.get("intent", {}).get("side"),
                "guard_status": order.get("guard_result", {}).get("status"),
                "created_at": order.get("created_at"),
            }
            for order in orders
        ],
    }


def show_intent(args: argparse.Namespace) -> dict[str, Any]:
    data = _load_staged()
    order = _find_order(data, args.intent_id)
    if order is None:
        return {"status": "not_found", "intent_id": args.intent_id}
    return {"status": "completed", "order": order}


def guard_intent(args: argparse.Namespace) -> dict[str, Any]:
    data = _load_staged()
    order = _find_order(data, args.intent_id)
    if order is None:
        return {"status": "not_found", "intent_id": args.intent_id}
    guard_result = run_guard_pipeline(order["intent"], refresh_market_data=not args.no_market_data)
    order["guard_result"] = guard_result
    order["status"] = _stage_status(guard_result)
    order["updated_at"] = now_iso()
    _save_staged(data)
    return {"status": order["status"], "intent_id": args.intent_id, "guard_status": guard_result["status"]}


def commit_intent(args: argparse.Namespace) -> dict[str, Any]:
    data = _load_staged()
    order = _find_order(data, args.intent_id)
    if order is None:
        return {"status": "not_found", "intent_id": args.intent_id}

    guard_result = run_guard_pipeline(order["intent"], refresh_market_data=not args.no_market_data)
    if guard_result["status"] == "REJECTED":
        order["guard_result"] = guard_result
        order["status"] = "STAGED_REJECTED"
        order["updated_at"] = now_iso()
        _save_staged(data)
        return {"status": "blocked_by_guard", "intent_id": args.intent_id, "guard_result": guard_result}

    commit_payload = {
        "timestamp": now_iso(),
        "intent_id": args.intent_id,
        "intent": guard_result["intent"],
        "guard_result": guard_result,
        "user_note": args.note or "",
        "execution_policy": "ADVISORY_ONLY_NO_BROKER_ORDER",
    }
    commit_hash = _hash_payload(commit_payload, 12)
    commit_record = {"commit_hash": commit_hash, **commit_payload}
    _append_jsonl(COMMITS_PATH, commit_record)
    _append_jsonl(
        EVENT_LOG_PATH,
        {
            "timestamp": now_iso(),
            "event_type": "order.committed",
            "intent_id": args.intent_id,
            "commit_hash": commit_hash,
            "symbol": guard_result["intent"]["symbol"],
            "guard_status": guard_result["status"],
        },
    )

    order["guard_result"] = guard_result
    order["status"] = "COMMITTED_REQUIRES_REVIEW" if guard_result["status"] == "REQUIRES_REVIEW" else "COMMITTED_READY"
    order["commit_hash"] = commit_hash
    order["updated_at"] = now_iso()
    _save_staged(data)
    append_trade_log(
        {
            "timestamp": commit_payload["timestamp"],
            "task": "order_intent_commit",
            "status": order["status"],
            "summary": f"Committed advisory intent {commit_hash} for {guard_result['intent']['symbol']}.",
            "report": "",
            "proposals": [commit_record],
        }
    )
    return {"status": order["status"], "intent_id": args.intent_id, "commit_hash": commit_hash}


def reject_intent(args: argparse.Namespace) -> dict[str, Any]:
    data = _load_staged()
    order = _find_order(data, args.intent_id)
    if order is None:
        return {"status": "not_found", "intent_id": args.intent_id}
    order["status"] = "REJECTED_BY_USER"
    order["rejected_at"] = now_iso()
    order["reject_reason"] = args.reason
    order["updated_at"] = now_iso()
    _save_staged(data)
    _append_jsonl(
        EVENT_LOG_PATH,
        {
            "timestamp": now_iso(),
            "event_type": "order.rejected",
            "intent_id": args.intent_id,
            "reason": args.reason,
            "symbol": order.get("intent", {}).get("symbol"),
        },
    )
    return {"status": "REJECTED_BY_USER", "intent_id": args.intent_id}


def export_review(args: argparse.Namespace) -> dict[str, Any]:
    data = _load_staged()
    path = ROOT / "reports" / f"{today_stamp()}_order_intents.md"
    lines = [
        "# Agentic Investor Order Intents",
        "",
        f"- Timestamp: {now_iso()}",
        "- Safety: advisory-only. No broker order was placed.",
        "",
        "| Intent | Status | Symbol | Side | Guard | Commit |",
        "|---|---|---|---|---|---|",
    ]
    for order in data.get("orders", []):
        lines.append(
            "| {intent_id} | {status} | {symbol} | {side} | {guard} | {commit} |".format(
                intent_id=order.get("intent_id"),
                status=order.get("status"),
                symbol=order.get("intent", {}).get("symbol"),
                side=order.get("intent", {}).get("side"),
                guard=order.get("guard_result", {}).get("status"),
                commit=order.get("commit_hash", ""),
            )
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return {"status": "completed", "report": str(path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Trading-as-Git advisory order intent workflow.")
    sub = parser.add_subparsers(dest="command", required=True)

    stage = sub.add_parser("stage", help="Stage an advisory order intent.")
    stage.add_argument("--symbol", required=True)
    stage.add_argument("--side", choices=["BUY", "SELL", "buy", "sell"], required=True)
    stage.add_argument("--instrument-type", default="EQUITY")
    stage.add_argument("--order-type", default="LIMIT")
    stage.add_argument("--quantity", type=float)
    stage.add_argument("--notional", type=float)
    stage.add_argument("--limit-price", type=float)
    stage.add_argument("--target-weight", type=float)
    stage.add_argument("--strategy", default="manual")
    stage.add_argument("--source", default="user")
    stage.add_argument("--rationale", required=True)
    stage.add_argument("--entry-trigger", default="")
    stage.add_argument("--invalidation", default="")
    stage.add_argument("--max-risk-pct", type=float)
    stage.add_argument("--note", default="")
    stage.add_argument("--no-market-data", action="store_true")
    stage.add_argument("--dry-run", action="store_true")

    list_cmd = sub.add_parser("list", help="List staged advisory intents.")
    list_cmd.add_argument("--all", action="store_true")

    show = sub.add_parser("show", help="Show a staged intent.")
    show.add_argument("--intent-id", required=True)

    guard = sub.add_parser("guard", help="Re-run guards for a staged intent.")
    guard.add_argument("--intent-id", required=True)
    guard.add_argument("--no-market-data", action="store_true")

    commit = sub.add_parser("commit", help="Commit an advisory intent after guards.")
    commit.add_argument("--intent-id", required=True)
    commit.add_argument("--note", default="")
    commit.add_argument("--no-market-data", action="store_true")

    reject = sub.add_parser("reject", help="Reject a staged intent.")
    reject.add_argument("--intent-id", required=True)
    reject.add_argument("--reason", required=True)

    sub.add_parser("review", help="Export a Markdown review of order intents.")

    args = parser.parse_args()
    if args.command == "stage":
        result = stage_intent(args)
    elif args.command == "list":
        result = list_intents(args)
    elif args.command == "show":
        result = show_intent(args)
    elif args.command == "guard":
        result = guard_intent(args)
    elif args.command == "commit":
        result = commit_intent(args)
    elif args.command == "reject":
        result = reject_intent(args)
    else:
        result = export_review(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
