from __future__ import annotations

import argparse
import json
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


WATCHLIST_PATH = ROOT / "watchlist.json"

DEFAULT_ALIASES = {
    "INTEL": "INTC",
    "$INTC": "INTC",
    "SANDISK": "SNDK",
    "$SNDK": "SNDK",
}


def _default_watchlist() -> dict[str, Any]:
    return {
        "version": "1.0",
        "last_updated": now_iso(),
        "policy": {
            "purpose": "Human-in-the-loop idea intake.",
            "default_status": "research_candidate",
            "order_policy": "Watchlist entries generate research only, never direct broker orders.",
        },
        "symbol_aliases": DEFAULT_ALIASES,
        "watchlist": [],
        "excluded": [],
        "sentiment_queue": [],
        "research_rules": {},
    }


def load_watchlist() -> dict[str, Any]:
    data = read_json(WATCHLIST_PATH, _default_watchlist())
    data.setdefault("symbol_aliases", {})
    data.setdefault("watchlist", [])
    data.setdefault("excluded", [])
    data.setdefault("sentiment_queue", [])
    return data


def save_watchlist(data: dict[str, Any]) -> None:
    data["last_updated"] = now_iso()
    write_json(WATCHLIST_PATH, data)


def normalize_symbol(symbol: str, aliases: dict[str, str] | None = None) -> str:
    raw_original = symbol.strip().upper()
    raw = raw_original.replace("$", "")
    for prefix in ("NASDAQ:", "NYSE:", "AMEX:", "OTC:", "US."):
        raw = raw.replace(prefix, "")
    raw = raw.replace(" ", "")
    merged_aliases = {**DEFAULT_ALIASES, **(aliases or {})}
    return merged_aliases.get(raw_original, merged_aliases.get(raw, raw))


def excluded_symbols(data: dict[str, Any]) -> set[str]:
    aliases = data.get("symbol_aliases", {})
    return {
        normalize_symbol(item.get("symbol", ""), aliases)
        for item in data.get("excluded", [])
        if item.get("symbol")
    }


def active_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    excluded = excluded_symbols(data)
    aliases = data.get("symbol_aliases", {})
    items = []
    for item in data.get("watchlist", []):
        normalized = normalize_symbol(item.get("normalized_symbol") or item.get("symbol", ""), aliases)
        if normalized in excluded:
            continue
        if item.get("status") in {"rejected", "archived"}:
            continue
        items.append({**item, "normalized_symbol": normalized})
    return items


def active_symbols(data: dict[str, Any]) -> list[str]:
    return sorted({item["normalized_symbol"] for item in active_items(data)})


def add_candidate(args: argparse.Namespace) -> dict[str, Any]:
    data = load_watchlist()
    aliases = data.get("symbol_aliases", {})
    normalized = normalize_symbol(args.symbol, aliases)
    existing = None
    for item in data["watchlist"]:
        if normalize_symbol(item.get("normalized_symbol") or item.get("symbol", ""), aliases) == normalized:
            existing = item
            break

    urls = [args.url] if args.url else []
    tags = args.tag or []
    if existing is None:
        existing = {
            "symbol": args.symbol.strip().upper(),
            "normalized_symbol": normalized,
            "status": args.status,
            "source": args.source,
            "added_at": now_iso(),
            "thesis": args.thesis or "",
            "urls": urls,
            "tags": tags,
            "conviction": "unrated",
        }
        data["watchlist"].append(existing)
    else:
        existing["status"] = args.status or existing.get("status", "research_candidate")
        existing["source"] = args.source or existing.get("source", "user")
        if args.thesis:
            existing["thesis"] = args.thesis
        for url in urls:
            if url not in existing.setdefault("urls", []):
                existing["urls"].append(url)
        for tag in tags:
            if tag not in existing.setdefault("tags", []):
                existing["tags"].append(tag)
        existing["updated_at"] = now_iso()

    save_watchlist(data)
    return {"status": "saved", "symbol": args.symbol, "normalized_symbol": normalized}


def exclude_candidate(args: argparse.Namespace) -> dict[str, Any]:
    data = load_watchlist()
    normalized = normalize_symbol(args.symbol, data.get("symbol_aliases", {}))
    excluded = data.setdefault("excluded", [])
    current = None
    for item in excluded:
        if normalize_symbol(item.get("symbol", ""), data.get("symbol_aliases", {})) == normalized:
            current = item
            break
    if current is None:
        excluded.append(
            {
                "symbol": normalized,
                "reason": args.reason or "",
                "excluded_at": now_iso(),
                "source": args.source,
            }
        )
    else:
        current["reason"] = args.reason or current.get("reason", "")
        current["updated_at"] = now_iso()
    save_watchlist(data)
    return {"status": "excluded", "symbol": normalized}


def unexclude_candidate(args: argparse.Namespace) -> dict[str, Any]:
    data = load_watchlist()
    normalized = normalize_symbol(args.symbol, data.get("symbol_aliases", {}))
    data["excluded"] = [
        item for item in data.get("excluded", [])
        if normalize_symbol(item.get("symbol", ""), data.get("symbol_aliases", {})) != normalized
    ]
    save_watchlist(data)
    return {"status": "unexcluded", "symbol": normalized}


def _scan_one(symbol: str) -> dict[str, Any]:
    try:
        from moomoo_data import fetch_research_snapshot

        research = fetch_research_snapshot([symbol])
        records = research.get("records", [])
        if records:
            row = records[0]
            return {
                "symbol": symbol,
                "data_status": "ok",
                "last_price": row.get("last_price"),
                "day_change_pct": row.get("day_change_pct"),
                "momentum_30d_pct": row.get("momentum_30d_pct"),
                "ma50": row.get("ma50"),
                "ma200": row.get("ma200"),
                "above_ma50": row.get("above_ma50"),
                "above_ma200": row.get("above_ma200"),
                "avg_volume_20d": row.get("avg_volume_20d"),
                "name": row.get("name"),
            }
        code = f"US.{symbol}"
        return {"symbol": symbol, "data_status": "no_history", "error": research.get("errors", {}).get(code, "No record returned.")}
    except Exception as exc:
        return {"symbol": symbol, "data_status": "error", "error": str(exc)}


def _decision(row: dict[str, Any]) -> str:
    if row.get("data_status") != "ok":
        return "research_only_data_gap"
    avg_volume = row.get("avg_volume_20d") or 0
    momentum = row.get("momentum_30d_pct")
    if avg_volume and avg_volume < 500_000:
        return "liquidity_review"
    if row.get("above_ma50") and momentum is not None and momentum > 0:
        return "watch_for_entry_setup"
    if momentum is not None and momentum < 0:
        return "wait_for_reversal_or_reject"
    return "needs_manual_thesis_review"


def run_watchlist_review(symbols: list[str] | None = None) -> tuple[str, list[dict[str, Any]]]:
    data = load_watchlist()
    selected = [normalize_symbol(symbol, data.get("symbol_aliases", {})) for symbol in symbols] if symbols else active_symbols(data)
    excluded = excluded_symbols(data)
    selected = [symbol for symbol in selected if symbol not in excluded]
    rows = [_scan_one(symbol) for symbol in selected]
    for row in rows:
        row["decision"] = _decision(row)

    report_path = REPORTS_DIR / f"{today_stamp()}_watchlist_review.md"
    lines = [
        "# Agentic Investor Watchlist Review",
        "",
        f"- Timestamp: {now_iso()}",
        f"- Active candidates: {', '.join(selected) if selected else 'none'}",
        f"- Excluded symbols: {', '.join(sorted(excluded)) if excluded else 'none'}",
        "- Safety: advisory-only. No account query, unlock, or order placement.",
        "",
        "## Candidate Intake",
        "",
        "| Symbol | Source | Status | Thesis |",
        "|---|---|---|---|",
    ]
    by_symbol = {item["normalized_symbol"]: item for item in active_items(data)}
    for symbol in selected:
        item = by_symbol.get(symbol, {})
        thesis = (item.get("thesis") or "").replace("|", "/")
        lines.append(f"| {symbol} | {item.get('source', '')} | {item.get('status', '')} | {thesis} |")

    lines += [
        "",
        "## Market Data Check",
        "",
        "| Symbol | Data | Last | 30D % | MA50 | MA200 | Decision |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {symbol} | {data} | {last} | {mom} | {ma50} | {ma200} | {decision} |".format(
                symbol=row["symbol"],
                data=row.get("data_status"),
                last=_fmt(row.get("last_price")),
                mom=_fmt(row.get("momentum_30d_pct")),
                ma50=_fmt(row.get("ma50")),
                ma200=_fmt(row.get("ma200")),
                decision=row.get("decision"),
            )
        )
    errors = [row for row in rows if row.get("error")]
    if errors:
        lines += ["", "## Data Gaps", ""]
        for row in errors:
            lines.append(f"- {row['symbol']}: {row.get('error')}")

    lines += [
        "",
        "## How To Use This",
        "",
        "- User can add ideas from Reddit/X/news as research candidates.",
        "- User can exclude a symbol at any time; excluded symbols are ignored by this review.",
        "- A social-buzz candidate still needs data support, liquidity, thesis, risk limit, macro check, and user approval before any portfolio proposal.",
    ]
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    append_trade_log(
        {
            "timestamp": now_iso(),
            "task": "watchlist_review",
            "status": "completed",
            "summary": f"Reviewed {len(rows)} active watchlist candidates.",
            "report": str(report_path.relative_to(ROOT)),
            "proposals": [
                {"type": "watchlist_decision", "symbol": row["symbol"], "decision": row["decision"]}
                for row in rows
            ],
        }
    )
    return str(report_path), rows


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage agentic-investor watchlist candidates.")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Add or update a research candidate.")
    add.add_argument("--symbol", required=True)
    add.add_argument("--thesis", default="")
    add.add_argument("--source", default="user")
    add.add_argument("--url", default="")
    add.add_argument("--tag", action="append", default=[])
    add.add_argument("--status", default="research_candidate")

    exclude = sub.add_parser("exclude", help="Exclude a symbol from reviews.")
    exclude.add_argument("--symbol", required=True)
    exclude.add_argument("--reason", default="")
    exclude.add_argument("--source", default="user")

    unexclude = sub.add_parser("unexclude", help="Remove a symbol from the excluded list.")
    unexclude.add_argument("--symbol", required=True)

    review = sub.add_parser("review", help="Review active candidates with read-only market data.")
    review.add_argument("--symbol", action="append", default=[])

    args = parser.parse_args()
    if args.command == "add":
        result = add_candidate(args)
    elif args.command == "exclude":
        result = exclude_candidate(args)
    elif args.command == "unexclude":
        result = unexclude_candidate(args)
    else:
        report, rows = run_watchlist_review(args.symbol or None)
        result = {"status": "completed", "report": report, "reviewed": len(rows)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
