from __future__ import annotations

import argparse
import html
import json
import re
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from agentic_investor_common import REPORTS_DIR, ROOT, TRADE_LOG_PATH, now_iso, read_json, today_stamp, write_json


CONFIG_PATH = ROOT / "congress-traders.json"
DATA_DIR = ROOT / "data" / "congress_trades"
LATEST_PATH = DATA_DIR / "latest.json"


ACTION_SIGN = {
    "purchase": 1.0,
    "buy": 1.0,
    "sale": -1.0,
    "sale_partial": -0.6,
    "sell": -1.0,
    "exchange": 0.0,
}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _fetch_text(url: str, timeout: int) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 agentic-investor-congress-trades/1.0",
            "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _strip_html(text: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text))


def _amount_mid(amount_range: str) -> float:
    nums = [float(item.replace(",", "")) for item in re.findall(r"\$?([0-9][0-9,]*)", amount_range or "")]
    if len(nums) >= 2:
        return (nums[0] + nums[1]) / 2
    if len(nums) == 1:
        return nums[0]
    return 0.0


def _parse_date(value: Any) -> date | None:
    text = _clean(value)
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _manual_trades(config: dict[str, Any], start: date) -> list[dict[str, Any]]:
    trades = []
    for row in config.get("manual_seed_transactions", []):
        trade_date = _parse_date(row.get("transaction_date"))
        if not trade_date or trade_date < start:
            continue
        trades.append(
            {
                "member_id": row.get("member_id"),
                "member": row.get("member"),
                "transaction_date": trade_date.isoformat(),
                "disclosure_date": row.get("disclosure_date"),
                "symbol": _clean(row.get("symbol")).upper(),
                "asset": row.get("asset", ""),
                "transaction_type": row.get("transaction_type", ""),
                "amount_range": row.get("amount_range", ""),
                "amount_mid": _amount_mid(row.get("amount_range", "")),
                "source": row.get("source", "manual_seed"),
                "url": "",
                "notes": row.get("notes", ""),
                "official_verification_required": True,
            }
        )
    return trades


def _extract_candidate_tickers(text: str) -> list[str]:
    symbols = []
    for token in re.findall(r"\b[A-Z]{1,5}(?:\.[A-Z])?\b", text):
        if token in {"USD", "ETF", "CEO", "LLC", "INC", "THE", "AND", "STOCK", "HOUSE", "SENATE"}:
            continue
        symbols.append(token)
    return symbols


def _fetch_profile_summary(member: dict[str, Any], timeout: int) -> list[dict[str, Any]]:
    summaries = []
    for url in member.get("profile_urls", []):
        try:
            text = _strip_html(_fetch_text(url, timeout))
            tickers = _extract_candidate_tickers(text)
            summaries.append(
                {
                    "member_id": member["id"],
                    "member": member["display_name"],
                    "url": url,
                    "status": "fetched",
                    "text_sample": text[:500],
                    "candidate_tickers": sorted(set(tickers))[:25],
                }
            )
        except Exception as exc:
            summaries.append(
                {
                    "member_id": member["id"],
                    "member": member["display_name"],
                    "url": url,
                    "status": "error",
                    "error": str(exc),
                }
            )
    return summaries


def _recency_weight(trade_date: date, today: date, lookback_days: int) -> float:
    age = max((today - trade_date).days, 0)
    return max(0.15, 1.0 - age / max(lookback_days, 1))


def _aggregate_signals(trades: list[dict[str, Any]], config: dict[str, Any], today: date, lookback_days: int) -> list[dict[str, Any]]:
    member_weights = {member["id"]: float(member.get("performance_weight", 0.5)) for member in config.get("tracked_members", [])}
    by_symbol: dict[str, dict[str, Any]] = {}
    for trade in trades:
        symbol = trade.get("symbol", "")
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,7}", symbol):
            continue
        trade_date = _parse_date(trade.get("transaction_date")) or today
        sign = ACTION_SIGN.get(str(trade.get("transaction_type", "")).lower(), 0.0)
        weight = member_weights.get(trade.get("member_id"), 0.5)
        amount = float(trade.get("amount_mid") or 0.0)
        amount_score = min(amount / 5_000_000, 1.0)
        score = sign * weight * amount_score * _recency_weight(trade_date, today, lookback_days)
        bucket = by_symbol.setdefault(
            symbol,
            {
                "symbol": symbol,
                "net_score": 0.0,
                "buy_count": 0,
                "sell_count": 0,
                "member_count": 0,
                "members": set(),
                "trades": [],
            },
        )
        bucket["net_score"] += score
        if sign > 0:
            bucket["buy_count"] += 1
        elif sign < 0:
            bucket["sell_count"] += 1
        bucket["members"].add(trade.get("member"))
        bucket["trades"].append({**trade, "signal_contribution": score})
    rows = []
    for bucket in by_symbol.values():
        bucket["member_count"] = len(bucket["members"])
        bucket["members"] = sorted(member for member in bucket["members"] if member)
        bucket["trades"].sort(key=lambda item: item.get("transaction_date", ""), reverse=True)
        rows.append(bucket)
    rows.sort(key=lambda item: abs(item["net_score"]), reverse=True)
    return rows


def build_congress_result(args: argparse.Namespace) -> dict[str, Any]:
    config = read_json(CONFIG_PATH, {})
    today = date.today()
    lookback = args.lookback_days or int(config.get("settings", {}).get("lookback_days", 180))
    start = today - timedelta(days=lookback)
    trades = _manual_trades(config, start)
    profiles: list[dict[str, Any]] = []
    if args.fetch:
        for member in config.get("tracked_members", []):
            profiles.extend(_fetch_profile_summary(member, args.timeout))
    signals = _aggregate_signals(trades, config, today, lookback)
    return {
        "task": "congress_trades_tracker",
        "timestamp": now_iso(),
        "lookback_days": lookback,
        "tracked_member_count": len(config.get("tracked_members", [])),
        "trade_count": len(trades),
        "trades": trades,
        "signals": signals,
        "profile_fetches": profiles,
        "official_sources": config.get("sources", []),
        "assumptions": {
            "execution": "advisory-only; no account query and no orders",
            "disclosure_lag": "Congressional trade disclosures can arrive after the transaction date and may be amended.",
            "verification": "Use official House/Senate disclosure pages before converting any signal into a trade thesis.",
            "signal_policy": "Congress signals are idea-source only and cannot override liquidity, macro, event, or risk guards.",
        },
    }


def write_outputs(result: dict[str, Any]) -> tuple[Path, Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    snapshot_path = DATA_DIR / f"{stamp}_congress_trades_tracker.json"
    write_json(snapshot_path, result)
    write_json(LATEST_PATH, result)

    report_path = REPORTS_DIR / f"{today_stamp()}_congress_trades_tracker.md"
    lines = [
        "# Congressional Trades Tracker",
        "",
        f"- Timestamp: {result['timestamp']}",
        f"- Lookback days: {result['lookback_days']}",
        f"- Tracked members: {result['tracked_member_count']}",
        f"- Parsed/seeded trades: {result['trade_count']}",
        "",
        "## Signal Summary",
        "",
        "| Symbol | Net Score | Buys | Sells | Members | Latest Trade |",
        "|---|---:|---:|---:|---|---|",
    ]
    for signal in result["signals"]:
        latest = signal.get("trades", [{}])[0]
        lines.append(
            "| {symbol} | {score:.3f} | {buys} | {sells} | {members} | {latest} {action} {amount} |".format(
                symbol=signal["symbol"],
                score=float(signal["net_score"]),
                buys=signal["buy_count"],
                sells=signal["sell_count"],
                members=", ".join(signal["members"]),
                latest=latest.get("transaction_date", ""),
                action=latest.get("transaction_type", ""),
                amount=latest.get("amount_range", ""),
            )
        )
    lines += [
        "",
        "## Recent Trades",
        "",
        "| Date | Disclosure | Member | Symbol | Action | Amount | Source |",
        "|---|---|---|---|---|---|---|",
    ]
    for trade in sorted(result["trades"], key=lambda item: item.get("transaction_date", ""), reverse=True):
        lines.append(
            "| {date} | {disclosure} | {member} | {symbol} | {action} | {amount} | {source} |".format(
                date=trade.get("transaction_date", ""),
                disclosure=trade.get("disclosure_date", ""),
                member=trade.get("member", ""),
                symbol=trade.get("symbol", ""),
                action=trade.get("transaction_type", ""),
                amount=trade.get("amount_range", ""),
                source=trade.get("source", ""),
            )
        )
    if result.get("profile_fetches"):
        lines += ["", "## Profile Fetch Status", "", "| Member | URL | Status | Candidate Tickers |", "|---|---|---|---|"]
        for row in result["profile_fetches"]:
            lines.append(
                "| {member} | {url} | {status} | {tickers} |".format(
                    member=row.get("member", ""),
                    url=row.get("url", ""),
                    status=row.get("status", ""),
                    tickers=", ".join(row.get("candidate_tickers", [])),
                )
            )
    lines += ["", "## Official Verification", ""]
    for source in result.get("official_sources", []):
        lines.append(f"- {source.get('id')}: {source.get('url')}")
    lines += ["", "## Safety", ""]
    for key, value in result.get("assumptions", {}).items():
        lines.append(f"- {key}: {value}")
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    log = read_json(TRADE_LOG_PATH, {"records": []})
    log.setdefault("records", []).append(
        {
            "timestamp": result["timestamp"],
            "task": "congress_trades_tracker",
            "status": "completed",
            "summary": f"Tracked {result['tracked_member_count']} members and produced {len(result['signals'])} congressional trade signals.",
            "report": str(report_path.relative_to(ROOT)),
            "proposals": [
                {
                    "type": "congress_trade_signal",
                    "signals": [
                        {"symbol": row["symbol"], "net_score": row["net_score"], "members": row["members"]}
                        for row in result["signals"][:10]
                    ],
                }
            ],
        }
    )
    write_json(TRADE_LOG_PATH, log)
    return report_path, snapshot_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Track congressional trade disclosures as advisory idea-source.")
    parser.add_argument("--lookback-days", type=int, default=None)
    parser.add_argument("--fetch", action="store_true", help="Fetch profile pages for lightweight status/ticker hints.")
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    result = build_congress_result(args)
    report, json_path = write_outputs(result)
    print(json.dumps({"status": "completed", "report": str(report), "json": str(json_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
