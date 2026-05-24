from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from agentic_investor_common import REPORTS_DIR, ROOT, now_iso, today_stamp, write_json
from openbb_data import (
    _load_obb,
    diagnostics,
    fetch_equity_history,
    fetch_true_macro_series,
)


DATA_DIR = ROOT / "data" / "openbb"
LATEST_PATH = DATA_DIR / "latest_smoke.json"


def _coverage_summary() -> dict[str, Any]:
    try:
        obb = _load_obb()
        coverage = getattr(obb, "coverage", None)
        providers = getattr(coverage, "providers", None) if coverage is not None else None
        if providers is None:
            return {"status": "MISSING", "providers": {}}
        return {"status": "PASS", "providers": providers}
    except Exception as exc:
        return {"status": "FAIL", "error": str(exc), "providers": {}}


def build_openbb_smoke() -> dict[str, Any]:
    timestamp = now_iso()
    start = (date.today() - timedelta(days=110)).isoformat()
    end = date.today().isoformat()
    diag = diagnostics()
    result: dict[str, Any] = {
        "task": "openbb_smoke",
        "timestamp": timestamp,
        "status": "FAIL",
        "diagnostics": diag,
        "dateRange": {"start": start, "end": end},
        "coverage": {},
        "equityRecords": {},
        "equityHistoryRows": {},
        "macroSeries": [],
        "errors": {},
        "nextInstallCommands": [
            f'powershell -ExecutionPolicy Bypass -File "{ROOT / "scripts" / "install_openbb.ps1"}"',
        ],
    }
    if not diag.get("available"):
        result["errors"]["openbb_import"] = diag.get("error")
        result["noteZh"] = (
            "OpenBB \u5c1a\u672a\u5b89\u88c5\u5230\u5f53\u524d Python "
            "\u73af\u5883\uff1b\u547d\u4ee4\u884c\u7f51\u7edc\u88ab\u62e6"
            "\u622a\u65f6\u65e0\u6cd5\u81ea\u52a8\u4e0b\u8f7d\u3002"
        )
        return result

    result["coverage"] = _coverage_summary()
    records, history, equity_errors = fetch_equity_history(["NVDA", "GOOG", "SPY", "QQQ"], start, end)
    macro, macro_errors = fetch_true_macro_series(start, end)
    result["equityRecords"] = records
    result["equityHistoryRows"] = {symbol: len(rows) for symbol, rows in history.items()}
    result["macroSeries"] = macro
    result["errors"] = {
        **{f"equity.{key}": value for key, value in equity_errors.items()},
        **{f"macro.{key}": value for key, value in macro_errors.items()},
    }
    if records and macro and not result["errors"]:
        result["status"] = "PASS"
    elif diag.get("available"):
        result["status"] = "WARN"
    else:
        result["status"] = "FAIL"
    result["noteZh"] = (
        "OpenBB \u5df2\u53ef\u5bfc\u5165\uff1b\u8bf7\u67e5\u770b equity/macro "
        "\u8986\u76d6\u548c provider \u9519\u8bef\u51b3\u5b9a\u4e0b\u4e00"
        "\u6b65\u5b89\u88c5\u54ea\u4e9b provider\u3002"
    )
    return result


def write_outputs(result: dict[str, Any]) -> tuple[Path, Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = DATA_DIR / f"{today_stamp()}_openbb_smoke.json"
    write_json(snapshot_path, result)
    write_json(LATEST_PATH, result)

    report = REPORTS_DIR / f"{today_stamp()}_openbb_smoke.md"
    lines = [
        "# OpenBB Smoke Test",
        "",
        f"- Timestamp: {result.get('timestamp')}",
        f"- Status: {result.get('status')}",
        f"- OpenBB available: {result.get('diagnostics', {}).get('available')}",
        f"- OpenBB version: {result.get('diagnostics', {}).get('version')}",
        f"- Note: {result.get('noteZh')}",
        "",
        "## Equity Records",
        "",
        "| Symbol | Last | Day % | 30D % | Above MA50 | Source |",
        "|---|---:|---:|---:|---|---|",
    ]
    for symbol, row in sorted(result.get("equityRecords", {}).items()):
        lines.append(
            f"| {symbol} | {row.get('last_price')} | {row.get('day_change_pct')} | "
            f"{row.get('momentum_30d_pct')} | {row.get('above_ma50')} | {row.get('source')} |"
        )
    if not result.get("equityRecords"):
        lines.append("| n/a | | | | | |")

    lines += [
        "",
        "## Macro Series",
        "",
        "| Symbol | Last | Unit | As Of | Source |",
        "|---|---:|---|---|---|",
    ]
    for row in result.get("macroSeries", []):
        lines.append(f"| {row.get('symbol')} | {row.get('last')} | {row.get('unit')} | {row.get('asOf')} | {row.get('source')} |")
    if not result.get("macroSeries"):
        lines.append("| n/a | | | | |")

    if result.get("errors"):
        lines += ["", "## Errors", ""]
        for key, value in sorted(result.get("errors", {}).items()):
            lines.append(f"- {key}: {value}")

    lines += [
        "",
        "## Install Commands",
        "",
    ]
    for command in result.get("nextInstallCommands", []):
        lines.append(f"- `{command}`")

    report.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report, snapshot_path


def main() -> int:
    result = build_openbb_smoke()
    report, snapshot = write_outputs(result)
    print(json.dumps({"task": "openbb_smoke", "status": result.get("status"), "report": str(report), "snapshot": str(snapshot)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
