from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from agentic_investor_common import REPORTS_DIR, ROOT, append_trade_log, now_iso, read_json, today_stamp, write_json
from watchlist_manager import active_items, active_symbols, load_watchlist


SOURCES_PATH = ROOT / "intelligence-sources.json"
DATA_DIR = ROOT / "data" / "intelligence"
DEFAULT_OPENCLI_PATH = Path.home() / "Documents" / "Codex" / ".tools" / "opencli.cmd"

POSITIVE_WORDS = {
    "beat",
    "beats",
    "bullish",
    "upgrade",
    "raises",
    "raised",
    "growth",
    "accelerates",
    "strong",
    "surge",
    "record",
    "optimistic",
    "outperform",
    "buy",
}

NEGATIVE_WORDS = {
    "miss",
    "misses",
    "bearish",
    "downgrade",
    "cuts",
    "cut",
    "slows",
    "weak",
    "falls",
    "drop",
    "probe",
    "lawsuit",
    "risk",
    "warning",
    "underperform",
    "sell",
}

EVENT_PATTERNS: dict[str, list[str]] = {
    "market_mover_up": [
        "stock jumps",
        "stock surges",
        "shares jump",
        "shares surge",
        "shares rally",
        "rallies",
        "jumps",
        "surges",
        "soars",
        "leaps",
        "opened up",
    ],
    "market_mover_down": [
        "stock falls",
        "stock sinks",
        "shares fall",
        "shares sink",
        "shares drop",
        "plunges",
        "sinks",
        "drops",
        "under pressure",
        "dives",
    ],
    "corporate_action": [
        "takeover",
        "acquisition",
        "acquire",
        "buyout",
        "merger",
        "bid",
        "offer",
        "unsolicited proposal",
    ],
    "regulatory_catalyst": [
        "regulation",
        "regulatory",
        "bill",
        "act",
        "sec",
        "doj",
        "fda",
        "approval",
        "probe",
    ],
    "earnings_surprise": [
        "earnings",
        "guidance",
        "forecast",
        "revenue",
        "profit",
        "loss",
        "beats",
        "misses",
    ],
    "analyst_action": [
        "upgrade",
        "downgrade",
        "price target",
        "initiates",
        "outperform",
        "underperform",
    ],
    "social_crowding": [
        "meme",
        "social crowding",
        "crowding",
        "reddit",
        "xiaohongshu",
        "retail attention",
        "risk overlay",
        "disagreement risk",
        "ranking",
        "小红书",
        "拥挤",
        "看跌仓位",
        "排名",
    ],
}

GENERIC_SYMBOL_STOPWORDS = {
    "CEO",
    "CFO",
    "SEC",
    "DOJ",
    "FDA",
    "IPO",
    "ETF",
    "AI",
    "US",
    "USA",
    "UK",
    "EU",
    "CNBC",
    "WSJ",
    "BBC",
}


def _default_sources() -> dict[str, Any]:
    return {
        "version": "1.0",
        "last_updated": now_iso(),
        "policy": {
            "purpose": "Daily/weekly intelligence monitoring.",
            "trading_policy": "Research only, not standalone trading signal.",
        },
        "themes": [],
        "thought_leaders": [],
        "public_feeds": [],
        "manual_link_queue": [],
        "x_api": {"enabled": False, "bearer_token_env": "X_BEARER_TOKEN"},
        "alert_rules": {},
    }


def load_sources() -> dict[str, Any]:
    data = read_json(SOURCES_PATH, _default_sources())
    data.setdefault("themes", [])
    data.setdefault("thought_leaders", [])
    data.setdefault("public_feeds", [])
    data.setdefault("manual_link_queue", [])
    data.setdefault("alert_rules", {})
    return data


def _strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def _parse_date(value: str) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).astimezone().isoformat(timespec="seconds")
    except Exception:
        return value


def _fetch_url(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "agentic-investor/1.0 research monitor",
            "Accept": "application/rss+xml, application/atom+xml, application/json, text/xml, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _parse_feed(source: dict[str, Any], raw: bytes, max_items: int) -> list[dict[str, Any]]:
    root = ET.fromstring(raw)
    items: list[dict[str, Any]] = []
    if root.tag.lower().endswith("rss") or root.find("./channel") is not None:
        for item in root.findall("./channel/item")[:max_items]:
            items.append(
                {
                    "source_id": source.get("id"),
                    "source_label": source.get("label"),
                    "source_priority": source.get("priority", 0.5),
                    "title": _strip_tags(item.findtext("title", "")),
                    "url": item.findtext("link", ""),
                    "published_at": _parse_date(item.findtext("pubDate", "")),
                    "summary": _strip_tags(item.findtext("description", ""))[:800],
                }
            )
        return items

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall("atom:entry", ns) or root.findall("entry")
    for entry in entries[:max_items]:
        link = ""
        for link_node in entry.findall("atom:link", ns) + entry.findall("link"):
            link = link_node.attrib.get("href", link)
        items.append(
            {
                "source_id": source.get("id"),
                "source_label": source.get("label"),
                "source_priority": source.get("priority", 0.5),
                "title": _strip_tags(entry.findtext("atom:title", "", ns) or entry.findtext("title", "")),
                "url": link,
                "published_at": entry.findtext("atom:updated", "", ns) or entry.findtext("updated", ""),
                "summary": _strip_tags(entry.findtext("atom:summary", "", ns) or entry.findtext("summary", ""))[:800],
            }
        )
    return items


def _parse_json_feed(source: dict[str, Any], raw: bytes, max_items: int) -> list[dict[str, Any]]:
    payload = json.loads(raw.decode("utf-8", errors="replace"))
    articles = payload.get("articles") or payload.get("results") or payload.get("data") or []
    items: list[dict[str, Any]] = []
    for article in articles[:max_items]:
        domain = article.get("domain") or article.get("source", {}).get("name") or article.get("source")
        items.append(
            {
                "source_id": source.get("id"),
                "source_label": source.get("label"),
                "source_priority": source.get("priority", 0.5),
                "title": _strip_tags(article.get("title") or article.get("name") or ""),
                "url": article.get("url") or article.get("link") or "",
                "published_at": article.get("seendate") or article.get("publishedAt") or article.get("date") or "",
                "summary": _strip_tags(
                    article.get("snippet")
                    or article.get("description")
                    or article.get("summary")
                    or domain
                    or ""
                )[:800],
                "publisher": domain,
            }
        )
    return items


def _manual_items(config: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    items = []
    for item in config.get("manual_link_queue", []):
        items.append(
            {
                "source_id": "manual_queue",
                "source_label": item.get("source", "manual"),
                "source_priority": 0.7,
                "title": item.get("title", item.get("url", "manual item")),
                "url": item.get("url", ""),
                "published_at": item.get("published_at", ""),
                "summary": item.get("note", ""),
            }
        )
    if args.manual_title or args.manual_url or args.manual_text:
        items.append(
            {
                "source_id": "manual_cli",
                "source_label": args.manual_source or "manual",
                "source_priority": 0.8,
                "title": args.manual_title or args.manual_url or "manual item",
                "url": args.manual_url or "",
                "published_at": now_iso(),
                "summary": args.manual_text or "",
            }
        )
    return items


def _opencli_path(config: dict[str, Any]) -> Path:
    configured = config.get("social_media_monitoring", {}).get("opencli_path") or os.environ.get("OPENCLI_PATH")
    return Path(configured) if configured else DEFAULT_OPENCLI_PATH


def _fetch_x_kol_items(config: dict[str, Any], args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if not args.fetch_x_kols:
        return [], {}
    opencli = _opencli_path(config)
    if not opencli.exists():
        return [], {"opencli": f"OpenCLI not found: {opencli}"}
    items: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for kol in config.get("social_media_monitoring", {}).get("x_kols", []):
        handle = str(kol.get("handle", "")).lstrip("@").strip()
        if not handle:
            continue
        command = [
            "cmd",
            "/c",
            str(opencli),
            "twitter",
            "tweets",
            handle,
            "--limit",
            str(args.max_x_tweets_per_kol),
            "-f",
            "json",
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=args.x_timeout,
            )
        except Exception as exc:
            errors[handle] = str(exc)
            continue
        if completed.returncode != 0:
            errors[handle] = (completed.stderr or completed.stdout or "").strip()[:800]
            continue
        try:
            tweets = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError as exc:
            errors[handle] = f"Could not parse OpenCLI JSON: {exc}"
            continue
        for tweet in tweets:
            text = _strip_tags(tweet.get("text") or "")
            items.append(
                {
                    "source_id": f"x_kol_{handle}",
                    "source_label": f"X KOL: {kol.get('name') or handle}",
                    "source_priority": kol.get("priority", 0.7),
                    "title": text[:140] or f"X post from @{handle}",
                    "url": tweet.get("url") or f"https://x.com/{handle}",
                    "published_at": _parse_date(tweet.get("created_at", "")),
                    "summary": text[:800],
                    "author": tweet.get("name") or kol.get("name") or handle,
                    "x_handle": handle,
                    "x_likes": tweet.get("likes"),
                    "x_retweets": tweet.get("retweets"),
                    "x_views": tweet.get("views"),
                    "source_note": kol.get("why", ""),
                }
            )
    return items, errors


def _keyword_hits(text: str, keywords: list[str]) -> list[str]:
    lower = text.lower()
    hits = []
    for keyword in keywords:
        key = str(keyword).strip()
        if key and key.lower() in lower:
            hits.append(key)
    return sorted(set(hits))


def _event_hits(text: str) -> list[str]:
    lower = text.lower()
    hits = []
    for event_type, patterns in EVENT_PATTERNS.items():
        if any(pattern in lower for pattern in patterns):
            hits.append(event_type)
    return sorted(set(hits))


def _candidate_symbols(text: str) -> list[str]:
    hits = set()
    for pattern in (r"\$([A-Z][A-Z0-9.\-]{0,6})\b", r"\(([A-Z][A-Z0-9.\-]{0,6})\)"):
        for match in re.findall(pattern, text or ""):
            symbol = match.upper()
            if symbol not in GENERIC_SYMBOL_STOPWORDS:
                hits.add(symbol)
    return sorted(hits)


def _diversify_event_radar(items: list[dict[str, Any]], max_per_cluster: int = 3) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    diversified: list[dict[str, Any]] = []
    for item in items:
        symbols = item.get("symbol_hits") or item.get("candidate_symbol_hits") or []
        key = ",".join(sorted(str(symbol) for symbol in symbols)) or str(item.get("source_id") or "unknown")
        if counts.get(key, 0) >= max_per_cluster:
            continue
        counts[key] = counts.get(key, 0) + 1
        diversified.append(item)
    return diversified


def _watch_symbol_keyword_map(watchlist: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in active_items(watchlist):
        symbol = str(item.get("normalized_symbol") or item.get("symbol") or "").upper()
        if not symbol:
            continue
        terms = {symbol, f"${symbol}", str(item.get("symbol") or "")}
        terms.update(str(keyword) for keyword in item.get("keywords", []) or [])
        for term in terms:
            cleaned = str(term).strip()
            if cleaned:
                mapping[cleaned] = symbol
    return mapping


def _symbol_hits(text: str, keyword_map: dict[str, str]) -> list[str]:
    hits = set()
    for keyword, symbol in keyword_map.items():
        key = str(keyword).strip()
        if not key:
            continue
        if key.upper() == str(symbol).upper() and len(key) <= 2:
            if re.search(rf"(?<![A-Za-z0-9])[$(]{re.escape(key)}[)]?(?![A-Za-z0-9])", text, flags=re.IGNORECASE):
                hits.add(symbol)
            continue
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])", text, flags=re.IGNORECASE):
            hits.add(symbol)
    return sorted(hits)


def _thought_leader_keywords(config: dict[str, Any]) -> list[str]:
    keywords: list[str] = []
    for leader in config.get("thought_leaders", []):
        keywords.append(str(leader.get("name", "")))
        keywords.extend(str(alias) for alias in leader.get("aliases", []))
        if leader.get("x_handle"):
            handle = str(leader["x_handle"]).lstrip("@")
            keywords.extend([handle, f"@{handle}"])
    for kol in config.get("social_media_monitoring", {}).get("x_kols", []):
        keywords.append(str(kol.get("name", "")))
        handle = str(kol.get("handle", "")).lstrip("@")
        if handle:
            keywords.extend([handle, f"@{handle}"])
    return keywords


def _sentiment_signal(text: str) -> dict[str, Any]:
    lower = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    tokens = lower.split()
    positive_hits = sorted({word for word in tokens if word in POSITIVE_WORDS})
    negative_hits = sorted({word for word in tokens if word in NEGATIVE_WORDS})
    raw_score = len(positive_hits) - len(negative_hits)
    denominator = max(1, len(positive_hits) + len(negative_hits))
    score = round(raw_score / denominator, 3)
    if score >= 0.25:
        label = "POSITIVE"
    elif score <= -0.25:
        label = "NEGATIVE"
    else:
        label = "NEUTRAL"
    return {
        "sentiment_label": label,
        "sentiment_score": score,
        "sentiment_positive_hits": positive_hits,
        "sentiment_negative_hits": negative_hits,
    }


def _score_item(item: dict[str, Any], config: dict[str, Any], watch_keyword_map: dict[str, str]) -> dict[str, Any]:
    rules = config.get("alert_rules", {})
    title_summary = f"{item.get('title', '')} {item.get('summary', '')}"
    theme_hits = _keyword_hits(title_summary, config.get("themes", []))
    thought_hits = _keyword_hits(title_summary, _thought_leader_keywords(config))
    symbol_hits = _symbol_hits(title_summary, watch_keyword_map)
    event_hits = _event_hits(title_summary)
    candidate_symbols = sorted(set(_candidate_symbols(title_summary)) - set(symbol_hits))
    score = 0.0
    score += len(symbol_hits) * float(rules.get("watchlist_symbol_hit_weight", 3))
    score += len(theme_hits) * float(rules.get("theme_hit_weight", 1))
    score += len(thought_hits) * float(rules.get("thought_leader_hit_weight", 2))
    score += len(event_hits) * float(rules.get("event_hit_weight", 2))
    score += min(2, len(candidate_symbols)) * float(rules.get("candidate_symbol_hit_weight", 1))
    score += float(item.get("source_priority") or 0) * float(rules.get("source_priority_weight", 2))
    sentiment = _sentiment_signal(title_summary)
    return {
        **item,
        "score": round(score, 2),
        "symbol_hits": symbol_hits,
        "candidate_symbol_hits": candidate_symbols,
        "event_hits": event_hits,
        "theme_hits": theme_hits,
        "thought_leader_hits": thought_hits,
        **sentiment,
    }


def build_intel_result(args: argparse.Namespace) -> dict[str, Any]:
    config = load_sources()
    watchlist = load_watchlist()
    watch_symbols = active_symbols(watchlist)
    watch_keyword_map = _watch_symbol_keyword_map(watchlist)
    items = _manual_items(config, args)
    source_errors: dict[str, str] = {}
    fetched_count = 0
    x_items, x_errors = _fetch_x_kol_items(config, args)
    items.extend(x_items)
    for key, value in x_errors.items():
        source_errors[f"x_kol_{key}"] = value

    if args.fetch:
        for source in config.get("public_feeds", []):
            try:
                raw = _fetch_url(source["url"], timeout=args.timeout)
                if source.get("type") == "rss":
                    fetched = _parse_feed(source, raw, args.max_items_per_source)
                elif source.get("type") in {"json", "gdelt_doc"}:
                    fetched = _parse_json_feed(source, raw, args.max_items_per_source)
                else:
                    continue
                fetched_count += len(fetched)
                items.extend(fetched)
            except Exception as exc:
                source_errors[source.get("id", source.get("url", "unknown"))] = str(exc)

    scored = [_score_item(item, config, watch_keyword_map) for item in items]
    scored.sort(key=lambda row: row["score"], reverse=True)
    min_score = float(config.get("alert_rules", {}).get("minimum_score_for_report", 2))
    highlights = [item for item in scored if item["score"] >= min_score]
    event_radar = [
        item for item in scored
        if item.get("event_hits") and (item.get("symbol_hits") or item.get("candidate_symbol_hits"))
    ]
    event_radar = _diversify_event_radar(event_radar)
    event_radar_fallback: dict[str, Any] | None = None
    if args.fetch and fetched_count == 0 and not event_radar and source_errors:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            today_name = f"{today_stamp()}_intel.json"
            candidates = sorted(
                [path for path in DATA_DIR.glob("*_intel.json") if path.is_file() and path.name != today_name],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                previous = read_json(candidates[0], {})
                previous_radar = list(previous.get("event_radar") or [])
                if previous_radar:
                    event_radar = previous_radar[: args.max_highlights]
                    event_radar_fallback = {
                        "path": str(candidates[0]),
                        "timestamp": previous.get("timestamp"),
                        "reason": "public sources blocked; reused last cached event radar",
                    }
        except Exception:
            event_radar_fallback = None
    x_cfg = config.get("x_api", {})
    x_ready = bool(x_cfg.get("enabled") and os.environ.get(x_cfg.get("bearer_token_env", "X_BEARER_TOKEN")))

    return {
        "task": "intel_monitor",
        "timestamp": now_iso(),
        "fetch_enabled": bool(args.fetch),
        "watchlist_symbols": watch_symbols,
        "source_count": len(config.get("public_feeds", [])),
        "fetched_item_count": fetched_count,
        "x_kol_count": len(config.get("social_media_monitoring", {}).get("x_kols", [])),
        "x_fetched_item_count": len(x_items),
        "manual_item_count": len(_manual_items(config, args)),
        "source_errors": source_errors,
        "event_radar_fallback": event_radar_fallback,
        "x_api_ready": x_ready,
        "x_api_note": "X API not configured; use user-supplied links or configure X_BEARER_TOKEN." if not x_ready else "X API token detected, but live X ingestion is not implemented in this lightweight pass.",
        "items": scored[: args.max_total_items],
        "highlights": highlights[: args.max_highlights],
        "event_radar": event_radar[: args.max_highlights],
        "sentiment_summary": {
            "positive": sum(1 for item in scored if item.get("sentiment_label") == "POSITIVE"),
            "neutral": sum(1 for item in scored if item.get("sentiment_label") == "NEUTRAL"),
            "negative": sum(1 for item in scored if item.get("sentiment_label") == "NEGATIVE"),
        },
    }


def write_outputs(result: dict[str, Any]) -> tuple[Path, Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    json_path = DATA_DIR / f"{today_stamp()}_intel.json"
    report_path = REPORTS_DIR / f"{today_stamp()}_intel_monitor.md"
    write_json(json_path, result)

    lines = [
        "# Agentic Investor Intelligence Monitor",
        "",
        f"- Timestamp: {result['timestamp']}",
        f"- Fetch enabled: {result['fetch_enabled']}",
        f"- Watchlist symbols: {', '.join(result['watchlist_symbols']) if result['watchlist_symbols'] else 'none'}",
        f"- Public sources configured: {result['source_count']}",
        f"- Fetched items: {result['fetched_item_count']}",
        f"- X KOLs configured: {result.get('x_kol_count', 0)}",
        f"- X KOL items fetched: {result.get('x_fetched_item_count', 0)}",
        f"- X readiness: {result['x_api_ready']}",
        f"- X note: {result['x_api_note']}",
        "- Safety: intelligence is research-only, not a standalone trading signal.",
        "",
        "## Highlights",
        "",
    ]
    if result["highlights"]:
        for item in result["highlights"]:
            lines += [
                f"### {item.get('title') or 'Untitled'}",
                "",
                f"- Score: {item['score']}",
                f"- Source: {item.get('source_label', item.get('source_id', 'unknown'))}",
                f"- URL: {item.get('url') or 'n/a'}",
                f"- Symbol hits: {', '.join(item.get('symbol_hits', [])) or 'none'}",
                f"- Candidate symbol hits: {', '.join(item.get('candidate_symbol_hits', [])) or 'none'}",
                f"- Event hits: {', '.join(item.get('event_hits', [])) or 'none'}",
                f"- Theme hits: {', '.join(item.get('theme_hits', [])) or 'none'}",
                f"- Thought leader hits: {', '.join(item.get('thought_leader_hits', [])) or 'none'}",
                f"- Sentiment: {item.get('sentiment_label')} ({item.get('sentiment_score')})",
                f"- Summary: {item.get('summary') or 'n/a'}",
                "",
            ]
    else:
        lines.append("- No highlight passed the score threshold in this run.")

    lines += ["", "## Event Radar", ""]
    if result.get("event_radar_fallback"):
        fallback = result["event_radar_fallback"]
        lines += [
            f"- Fallback enabled: {fallback.get('reason')}",
            f"- Fallback source: {fallback.get('path')}",
            f"- Fallback timestamp: {fallback.get('timestamp')}",
            "",
        ]
    if result["event_radar"]:
        for item in result["event_radar"]:
            symbols = item.get("symbol_hits", []) + item.get("candidate_symbol_hits", [])
            lines += [
                f"### {item.get('title') or 'Untitled'}",
                "",
                f"- Event hits: {', '.join(item.get('event_hits', [])) or 'none'}",
                f"- Symbols: {', '.join(symbols) or 'none'}",
                f"- Source: {item.get('source_label', item.get('source_id', 'unknown'))}",
                f"- URL: {item.get('url') or 'n/a'}",
                "- Implication: force review of affected watchlist/playbook; do not auto-trade from this item alone.",
                "",
            ]
    else:
        lines.append("- No broad event radar hit in this run.")

    if result["source_errors"]:
        lines += ["", "## Source Errors", ""]
        for source_id, error in sorted(result["source_errors"].items()):
            lines.append(f"- {source_id}: {error}")

    lines += [
        "",
        "## Next Actions",
        "",
        "- Add interesting links to `watchlist.json` or `intelligence-sources.json.manual_link_queue`.",
        "- Promote only thesis-backed items into watchlist review.",
        "- Configure X API only if you want direct X monitoring; otherwise paste X links manually.",
    ]
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    append_trade_log(
        {
            "timestamp": result["timestamp"],
            "task": "intel_monitor",
            "status": "completed",
            "summary": f"Generated intelligence monitor with {len(result['highlights'])} highlights.",
            "report": str(report_path.relative_to(ROOT)),
            "proposals": [
                {
                    "type": "intelligence_highlight",
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "score": item.get("score"),
                    "symbol_hits": item.get("symbol_hits", []),
                    "candidate_symbol_hits": item.get("candidate_symbol_hits", []),
                    "event_hits": item.get("event_hits", []),
                }
                for item in result["highlights"]
            ]
            + [
                {
                    "type": "event_radar",
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "score": item.get("score"),
                    "symbol_hits": item.get("symbol_hits", []),
                    "candidate_symbol_hits": item.get("candidate_symbol_hits", []),
                    "event_hits": item.get("event_hits", []),
                }
                for item in result["event_radar"]
            ],
        }
    )
    return report_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor AI/finance/watchlist intelligence sources.")
    parser.add_argument("--fetch", action="store_true", help="Fetch configured public RSS feeds.")
    parser.add_argument("--fetch-x-kols", action="store_true", help="Fetch configured X KOL tweets through OpenCLI Browser Bridge.")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--x-timeout", type=int, default=90)
    parser.add_argument("--max-x-tweets-per-kol", type=int, default=5)
    parser.add_argument("--max-items-per-source", type=int, default=15)
    parser.add_argument("--max-total-items", type=int, default=80)
    parser.add_argument("--max-highlights", type=int, default=12)
    parser.add_argument("--manual-title", default="")
    parser.add_argument("--manual-url", default="")
    parser.add_argument("--manual-text", default="")
    parser.add_argument("--manual-source", default="")
    args = parser.parse_args()
    result = build_intel_result(args)
    report_path, json_path = write_outputs(result)
    print(json.dumps({"status": "completed", "report": str(report_path), "json": str(json_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
