from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VENDOR_PYTHON = ROOT / "vendor" / "python"
MOOMOO_APPDATA = ROOT / "vendor" / "runtime-appdata"


def _load_local_env() -> None:
    for name in (".env", ".env.local"):
        path = ROOT / name
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_local_env()
MOOMOO_APPDATA.mkdir(parents=True, exist_ok=True)
os.environ["APPDATA"] = str(MOOMOO_APPDATA)
os.environ["appdata"] = str(MOOMOO_APPDATA)
STATE_PATH = ROOT / "state.json"
RULE_PATH = ROOT / "rule-engine.json"
TRADE_LOG_PATH = ROOT / "trade-log.json"
REPORTS_DIR = ROOT / "reports"


def enable_vendor_python() -> None:
    """Enable project-local SDK fallbacks for integrations that still need them."""
    if os.environ.get("AGENTIC_ENABLE_VENDOR_PYTHON") == "0":
        return
    vendor_path = str(VENDOR_PYTHON)
    if VENDOR_PYTHON.exists() and vendor_path not in sys.path:
        sys.path.append(vendor_path)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def json_safe(data: Any) -> Any:
    if isinstance(data, float):
        return data if math.isfinite(data) else None
    if isinstance(data, list):
        return [json_safe(item) for item in data]
    if isinstance(data, tuple):
        return [json_safe(item) for item in data]
    if isinstance(data, dict):
        return {str(key): json_safe(value) for key, value in data.items()}
    return data


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(json_safe(data), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def load_context() -> tuple[dict[str, Any], dict[str, Any]]:
    return read_json(STATE_PATH), read_json(RULE_PATH)


def save_state(state: dict[str, Any]) -> None:
    write_json(STATE_PATH, state)


def current_safety_status(state: dict[str, Any]) -> dict[str, Any]:
    mode = state.get("mode", "SIMULATE")
    execution_mode = state.get("execution_mode", "ADVISORY_ONLY")
    allow_order_placement = bool(state.get("allow_order_placement", False))
    can_place_simulated = (
        mode == "SIMULATE"
        and execution_mode == "SIMULATE_AUTO"
        and allow_order_placement
        and not state.get("paused", False)
    )
    return {
        "paused": bool(state.get("paused", False)),
        "mode": mode,
        "execution_mode": execution_mode,
        "allow_order_placement": allow_order_placement,
        "can_place_simulated_orders": can_place_simulated,
        "can_place_real_orders": False,
    }


def top_level_allocation(rules: dict[str, Any], market_state: str | None) -> dict[str, Any]:
    market_rules = rules.get("market_state_rules", {})
    key = market_state if market_state in market_rules else None
    if key is None:
        return {
            "market_state": market_state,
            "allocation": None,
            "note": "No current market_state. Generate analysis only.",
        }
    return {
        "market_state": key,
        "allocation": market_rules[key].get("allocation", {}),
        "note": "Top-level allocation comes from market_state_rules.",
    }


def append_trade_log(entry: dict[str, Any]) -> None:
    log = read_json(TRADE_LOG_PATH, {"records": []})
    log.setdefault("records", []).append(entry)
    write_json(TRADE_LOG_PATH, log)


def write_report(task: str, lines: list[str]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{today_stamp()}_{task}.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def finish_task(
    task: str,
    state: dict[str, Any],
    status: str,
    summary: str,
    report_lines: list[str],
    proposals: list[dict[str, Any]] | None = None,
) -> int:
    timestamp = now_iso()
    state["last_check"] = timestamp
    state["last_task"] = task
    save_state(state)

    report_path = write_report(task, report_lines)
    append_trade_log(
        {
            "timestamp": timestamp,
            "task": task,
            "status": status,
            "summary": summary,
            "report": str(report_path.relative_to(ROOT)),
            "proposals": proposals or [],
        }
    )

    print(json.dumps({
        "task": task,
        "status": status,
        "summary": summary,
        "report": str(report_path),
    }, ensure_ascii=False, indent=2))
    return 0


def base_report_header(task: str, state: dict[str, Any], rules: dict[str, Any]) -> list[str]:
    safety = current_safety_status(state)
    allocation = top_level_allocation(rules, state.get("market_state"))
    return [
        f"# Agentic Investor Report: {task}",
        "",
        f"- Timestamp: {now_iso()}",
        f"- Mode: {safety['mode']}",
        f"- Execution mode: {safety['execution_mode']}",
        f"- Paused: {safety['paused']}",
        f"- Order placement allowed: {safety['allow_order_placement']}",
        f"- Real order placement: {safety['can_place_real_orders']}",
        f"- Market state: {allocation['market_state']}",
        f"- Allocation note: {allocation['note']}",
        "",
    ]


def advisory_footer() -> list[str]:
    return [
        "",
        "## Execution",
        "",
        "No live order was placed by this script. Treat all actions as proposals unless a separate reviewed executor is added.",
    ]
