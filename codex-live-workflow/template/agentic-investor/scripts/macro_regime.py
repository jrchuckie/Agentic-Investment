from __future__ import annotations

import argparse
import json
from typing import Any

from agentic_investor_common import REPORTS_DIR, ROOT, append_trade_log, now_iso, read_json, today_stamp, write_json


MACRO_PATH = ROOT / "macro-regime.json"


def _default_macro() -> dict[str, Any]:
    return {
        "version": "1.0",
        "last_updated": now_iso(),
        "policy": {
            "purpose": "Macro risk overlay.",
            "order_policy": "Macro state can reduce risk, but never creates direct buy orders.",
        },
        "current": {
            "fed_funds_target_range": None,
            "fed_policy_bias": "UNKNOWN",
            "rate_path_bias": "UNKNOWN",
            "inflation_risk": "UNKNOWN",
            "growth_risk": "UNKNOWN",
            "liquidity_regime": "UNKNOWN",
            "political_event_risk": "NORMAL",
            "notes": "",
        },
        "exposure_overlays": {},
    }


def load_macro() -> dict[str, Any]:
    data = read_json(MACRO_PATH, _default_macro())
    data.setdefault("current", {})
    data.setdefault("exposure_overlays", {})
    return data


def save_macro(data: dict[str, Any]) -> None:
    data["last_updated"] = now_iso()
    write_json(MACRO_PATH, data)


def classify_macro(data: dict[str, Any]) -> str:
    current = data.get("current", {})
    bias = str(current.get("fed_policy_bias") or "UNKNOWN").upper()
    rate_path = str(current.get("rate_path_bias") or "UNKNOWN").upper()
    political = str(current.get("political_event_risk") or "NORMAL").upper()
    liquidity = str(current.get("liquidity_regime") or "UNKNOWN").upper()

    if "RISK_OFF" in {bias, rate_path, political, liquidity}:
        return "RISK_OFF"
    if bias in {"HAWKISH", "TIGHTENING"} or rate_path in {"HIGHER_FOR_LONGER", "HIKES_POSSIBLE"}:
        return "HAWKISH"
    if bias in {"DOVISH", "EASING"} and liquidity in {"EASY", "IMPROVING", "UNKNOWN"}:
        return "RISK_ON"
    return "UNKNOWN"


def overlay_for(data: dict[str, Any], regime: str) -> dict[str, Any]:
    overlays = data.get("exposure_overlays", {})
    return overlays.get(regime) or overlays.get("UNKNOWN") or {
        "max_gross_exposure": 0.85,
        "minimum_cash": 0.15,
        "new_options_allowed": False,
        "note": "Fallback unknown macro overlay.",
    }


def run_macro_review() -> tuple[str, dict[str, Any]]:
    data = load_macro()
    regime = classify_macro(data)
    overlay = overlay_for(data, regime)
    current = data.get("current", {})
    report_path = REPORTS_DIR / f"{today_stamp()}_macro_regime.md"
    lines = [
        "# Agentic Investor Macro Regime Review",
        "",
        f"- Timestamp: {now_iso()}",
        f"- Macro regime: {regime}",
        f"- Fed funds target range: {current.get('fed_funds_target_range') or 'unknown'}",
        f"- Fed policy bias: {current.get('fed_policy_bias') or 'UNKNOWN'}",
        f"- Rate path bias: {current.get('rate_path_bias') or 'UNKNOWN'}",
        f"- Inflation risk: {current.get('inflation_risk') or 'UNKNOWN'}",
        f"- Growth risk: {current.get('growth_risk') or 'UNKNOWN'}",
        f"- Liquidity regime: {current.get('liquidity_regime') or 'UNKNOWN'}",
        f"- Political/event risk: {current.get('political_event_risk') or 'NORMAL'}",
        "",
        "## Exposure Overlay",
        "",
        f"- Max gross exposure: {overlay.get('max_gross_exposure')}",
        f"- Minimum cash: {overlay.get('minimum_cash')}",
        f"- New options allowed: {overlay.get('new_options_allowed')}",
        f"- Note: {overlay.get('note')}",
        "",
        "## Required Fresh Checks",
        "",
        "- Official Fed target range / FOMC statement.",
        "- Market-implied rate path or high-quality rate commentary.",
        "- 10Y yield, VIX, and liquidity proxy.",
        "- Major policy risks for semiconductors, AI, export controls, tariffs, and elections.",
        "",
        "## Safety",
        "",
        "Macro overlay is advisory-only. It did not query accounts, unlock trading, or place orders.",
    ]
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    append_trade_log(
        {
            "timestamp": now_iso(),
            "task": "macro_regime",
            "status": "completed",
            "summary": f"Macro regime classified as {regime}; max gross exposure {overlay.get('max_gross_exposure')}.",
            "report": str(report_path.relative_to(ROOT)),
            "proposals": [{"type": "macro_overlay", "regime": regime, "overlay": overlay}],
        }
    )
    return str(report_path), {"regime": regime, "overlay": overlay}


def set_fed(args: argparse.Namespace) -> dict[str, Any]:
    data = load_macro()
    current = data.setdefault("current", {})
    if args.target_range:
        current["fed_funds_target_range"] = args.target_range
    if args.bias:
        current["fed_policy_bias"] = args.bias.upper()
    if args.rate_path:
        current["rate_path_bias"] = args.rate_path.upper()
    if args.note:
        current["notes"] = args.note
    if args.source:
        current["fed_source"] = args.source
    save_macro(data)
    regime = classify_macro(data)
    return {"status": "saved", "macro_regime": regime, "current": current}


def main() -> int:
    parser = argparse.ArgumentParser(description="Review or update macro policy overlay.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("review", help="Generate macro regime review.")

    fed = sub.add_parser("set-fed", help="Set Fed target/rate-path notes from a verified source.")
    fed.add_argument("--target-range", default="")
    fed.add_argument("--bias", default="")
    fed.add_argument("--rate-path", default="")
    fed.add_argument("--source", default="")
    fed.add_argument("--note", default="")

    args = parser.parse_args()
    if args.command == "set-fed":
        result = set_fed(args)
    else:
        report, summary = run_macro_review()
        result = {"status": "completed", "report": report, **summary}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
