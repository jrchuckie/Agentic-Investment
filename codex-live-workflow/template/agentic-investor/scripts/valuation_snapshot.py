from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from agentic_investor_common import REPORTS_DIR, ROOT, now_iso, today_stamp, write_json
from market_snapshot import WATCH_SYMBOLS
from openbb_data import (
    fetch_equity_estimates_consensus,
    fetch_equity_fundamental_metrics,
)


DATA_DIR = ROOT / "data" / "market"
LATEST_PATH = DATA_DIR / "valuation_latest.json"


def _round(value: Any, digits: int = 2) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        return round(number, digits)
    except (TypeError, ValueError):
        return None


def _score_valuation(row: dict[str, Any]) -> dict[str, Any]:
    pe = _round(row.get("forwardPE") or row.get("peRatio"), 2)
    peg = _round(row.get("pegRatio"), 2)
    target_upside = _round(row.get("targetUpsidePct"), 2)
    revenue_growth = _round(row.get("revenueGrowth"), 4)
    profit_margin = _round(row.get("profitMargin"), 4)
    debt_to_equity = _round(row.get("debtToEquity"), 2)

    score = 50.0
    flags: list[str] = []

    if pe is None:
        score -= 10
        flags.append("PE\u7f3a\u5931")
    elif pe <= 25:
        score += 12
        flags.append("PE\u5408\u7406")
    elif pe <= 45:
        score += 4
        flags.append("PE\u504f\u9ad8\u4f46\u53ef\u63a5\u53d7")
    elif pe <= 80:
        score -= 8
        flags.append("PE\u504f\u8d35")
    else:
        score -= 18
        flags.append("PE\u5f88\u8d35")

    if peg is not None:
        if peg <= 1.5:
            score += 8
            flags.append("PEG\u652f\u6301\u589e\u957f")
        elif peg > 3:
            score -= 6
            flags.append("PEG\u504f\u8d35")

    if target_upside is None:
        score -= 8
        flags.append("\u76ee\u6807\u4ef7\u7f3a\u5931")
    elif target_upside >= 20:
        score += 10
        flags.append("\u76ee\u6807\u4ef7\u4e0a\u884c\u7a7a\u95f4\u5927")
    elif target_upside >= 8:
        score += 4
        flags.append("\u76ee\u6807\u4ef7\u6709\u7a7a\u95f4")
    elif target_upside < 0:
        score -= 12
        flags.append("\u76ee\u6807\u4ef7\u4f4e\u4e8e\u73b0\u4ef7")
    else:
        score -= 5
        flags.append("\u76ee\u6807\u4ef7\u7a7a\u95f4\u6709\u9650")

    if revenue_growth is not None:
        if revenue_growth >= 0.20:
            score += 8
            flags.append("\u6536\u5165\u9ad8\u589e\u957f")
        elif revenue_growth < 0:
            score -= 8
            flags.append("\u6536\u5165\u8d1f\u589e\u957f")

    if profit_margin is not None:
        if profit_margin >= 0.15:
            score += 6
            flags.append("\u5229\u6da6\u7387\u5065\u5eb7")
        elif profit_margin < 0:
            score -= 8
            flags.append("\u4ecd\u5728\u4e8f\u635f")

    if debt_to_equity is not None and debt_to_equity > 150:
        score -= 5
        flags.append("\u6760\u6746\u504f\u9ad8")

    score = max(0.0, min(100.0, score))
    if score >= 72:
        bucket = "\u4f30\u503c/\u8d22\u52a1\u652f\u6301\u4e70\u5165"
    elif score >= 55:
        bucket = "\u4f30\u503c\u4e2d\u6027\uff0c\u9700\u4ef7\u683c\u786e\u8ba4"
    elif score >= 40:
        bucket = "\u4f30\u503c\u504f\u5f31\uff0c\u53ea\u80fd\u89c2\u5bdf"
    else:
        bucket = "\u4f30\u503c\u4e0d\u652f\u6301\u4e3b\u52a8\u4e70\u5165"

    return {
        "valuationScore": _round(score, 1),
        "valuationBucketZh": bucket,
        "valuationFlagsZh": flags[:5],
    }


def build_valuation_snapshot(symbols: list[str] | None = None) -> dict[str, Any]:
    symbols = sorted({symbol.upper() for symbol in (symbols or WATCH_SYMBOLS) if symbol})
    fundamentals, fundamental_errors = fetch_equity_fundamental_metrics(symbols)
    consensus, consensus_errors = fetch_equity_estimates_consensus(symbols)

    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        fundamental = fundamentals.get(symbol, {})
        estimate = consensus.get(symbol, {})
        target = estimate.get("targetConsensus") or estimate.get("targetMedian")
        current_price = estimate.get("currentPrice")
        target_upside = None
        if target is not None and current_price:
            target_upside = (float(target) - float(current_price)) / abs(float(current_price)) * 100

        row = {
            "symbol": symbol,
            "companyName": estimate.get("companyName"),
            "peRatio": fundamental.get("peRatio"),
            "forwardPE": fundamental.get("forwardPE"),
            "pegRatio": fundamental.get("pegRatio"),
            "epsTtm": fundamental.get("epsTtm"),
            "epsForward": fundamental.get("epsForward"),
            "revenueGrowth": fundamental.get("revenueGrowth"),
            "earningsGrowth": fundamental.get("earningsGrowth"),
            "grossMargin": fundamental.get("grossMargin"),
            "operatingMargin": fundamental.get("operatingMargin"),
            "profitMargin": fundamental.get("profitMargin"),
            "returnOnEquity": fundamental.get("returnOnEquity"),
            "debtToEquity": fundamental.get("debtToEquity"),
            "currentRatio": fundamental.get("currentRatio"),
            "priceToSales": fundamental.get("priceToSales"),
            "enterpriseToRevenue": fundamental.get("enterpriseToRevenue"),
            "enterpriseToEbitda": fundamental.get("enterpriseToEbitda"),
            "beta": fundamental.get("beta"),
            "marketCap": fundamental.get("marketCap"),
            "currentPrice": current_price,
            "averageTargetPrice": target,
            "targetHigh": estimate.get("targetHigh"),
            "targetLow": estimate.get("targetLow"),
            "targetMedian": estimate.get("targetMedian"),
            "targetUpsidePct": _round(target_upside, 2),
            "recommendation": estimate.get("recommendation"),
            "recommendationMean": estimate.get("recommendationMean"),
            "numberOfAnalysts": estimate.get("numberOfAnalysts"),
            "currency": estimate.get("currency"),
            "source": "OpenBB yfinance: fundamental.metrics + estimates.consensus",
            "fundamentalSource": fundamental.get("source"),
            "estimateSource": estimate.get("source"),
            "asOf": fundamental.get("asOf") or now_iso(),
        }
        row.update(_score_valuation(row))
        if fundamental or estimate:
            row["dataQuality"] = "PASS"
        elif symbol in fundamental_errors or symbol in consensus_errors:
            row["dataQuality"] = "ERROR"
        else:
            row["dataQuality"] = "MISSING"
        rows.append(row)

    pass_count = sum(1 for row in rows if row.get("dataQuality") == "PASS")
    status = "PASS" if pass_count else "FAIL"
    if pass_count and (fundamental_errors or consensus_errors):
        status = "WARN"

    return {
        "timestamp": now_iso(),
        "status": status,
        "source": "OpenBB-first valuation snapshot",
        "symbolsRequested": len(symbols),
        "symbolsWithData": pass_count,
        "rows": rows,
        "symbols": {row["symbol"]: row for row in rows},
        "errors": {
            "fundamentalMetrics": fundamental_errors,
            "estimatesConsensus": consensus_errors,
        },
        "policy": {
            "advisoryOnly": True,
            "realAccountRead": False,
            "realBrokerOrder": False,
        },
    }


def write_outputs(result: dict[str, Any]) -> tuple[Path, Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if result.get("status") == "FAIL":
        dated = DATA_DIR / f"{today_stamp()}_valuation_snapshot_failed.json"
    else:
        dated = DATA_DIR / f"{today_stamp()}_valuation_snapshot.json"
    write_json(dated, result)
    if result.get("status") != "FAIL":
        write_json(LATEST_PATH, result)
    elif not LATEST_PATH.exists():
        write_json(LATEST_PATH, result)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_suffix = "valuation_snapshot_failed" if result.get("status") == "FAIL" else "valuation_snapshot"
    report = REPORTS_DIR / f"{today_stamp()}_{report_suffix}.md"
    lines = [
        "# Valuation Snapshot",
        "",
        f"- Status: {result.get('status')}",
        f"- Timestamp: {result.get('timestamp')}",
        f"- Symbols requested: {result.get('symbolsRequested')}",
        f"- Symbols with data: {result.get('symbolsWithData')}",
        f"- Source: {result.get('source')}",
        "",
        "| Symbol | PE | Fwd PE | Target | Upside | Recommendation | Score | Bucket |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |",
    ]
    for row in result.get("rows", [])[:120]:
        lines.append(
            f"| {row.get('symbol')} | {row.get('peRatio')} | {row.get('forwardPE')} | "
            f"{row.get('averageTargetPrice')} | {row.get('targetUpsidePct')} | "
            f"{row.get('recommendation')} | {row.get('valuationScore')} | {row.get('valuationBucketZh')} |"
        )
    lines.extend(
        [
            "",
            "## Errors",
            "",
            "```json",
            json.dumps(result.get("errors", {}), ensure_ascii=False, indent=2)[:8000],
            "```",
        ]
    )
    report.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report, LATEST_PATH


def main() -> int:
    result = build_valuation_snapshot()
    report, latest = write_outputs(result)
    print(json.dumps({
        "task": "valuation_snapshot",
        "status": result.get("status"),
        "symbolsWithData": result.get("symbolsWithData"),
        "report": str(report),
        "latest": str(latest),
    }, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"PASS", "WARN"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
