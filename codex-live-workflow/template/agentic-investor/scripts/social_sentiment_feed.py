from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_investor_common import REPORTS_DIR, ROOT, append_trade_log, now_iso, read_json, today_stamp, write_json


DATA_DIR = ROOT / "data" / "social_sentiment"
INTEL_DIR = ROOT / "data" / "intelligence"

CORE_SYMBOL_ALIASES: dict[str, list[str]] = {
    "NVDA": ["NVDA", "$NVDA", "NVIDIA", "Jensen Huang"],
    "AMD": ["AMD", "$AMD", "Advanced Micro Devices", "MI300", "MI350"],
    "GOOG": ["GOOG", "GOOGL", "$GOOG", "$GOOGL", "Google", "Alphabet", "Gemini"],
    "PLTR": ["PLTR", "$PLTR", "Palantir"],
    "AMZN": ["AMZN", "$AMZN", "Amazon", "AWS"],
    "MSFT": ["MSFT", "$MSFT", "Microsoft", "Azure", "OpenAI"],
    "META": ["META", "$META", "Meta", "Llama"],
    "TSLA": ["TSLA", "$TSLA", "Tesla", "robotaxi"],
    "AVGO": ["AVGO", "$AVGO", "Broadcom"],
    "TSM": ["TSM", "$TSM", "TSMC"],
    "ASML": ["ASML", "$ASML"],
    "ARM": ["$ARM", "Arm Holdings"],
    "MRVL": ["MRVL", "$MRVL", "Marvell"],
    "MU": ["MU", "$MU", "Micron", "HBM"],
    "ORCL": ["ORCL", "$ORCL", "Oracle"],
    "ON": ["$ON", "ON Semiconductor", "Onsemi"],
    "APP": ["$APP", "AppLovin"],
    "BILL": ["$BILL", "BILL Holdings"],
    "TEM": ["$TEM", "Tempus AI"],
    "RDDT": ["RDDT", "$RDDT", "Reddit"],
    "QQQ": ["QQQ", "$QQQ", "Nasdaq 100", "NASDAQ"],
    "SPY": ["SPY", "$SPY", "S&P 500"],
    "SMH": ["SMH", "$SMH", "semiconductor ETF", "semis"],
    "SOXX": ["SOXX", "$SOXX"],
    "SMCI": ["SMCI", "$SMCI", "Super Micro", "Supermicro"],
    "DELL": ["DELL", "$DELL", "Dell"],
    "ANET": ["ANET", "$ANET", "Arista"],
    "VRT": ["VRT", "$VRT", "Vertiv"],
    "GEV": ["GEV", "$GEV", "GE Vernova"],
    "PWR": ["PWR", "$PWR", "Quanta Services"],
    "ETN": ["ETN", "$ETN", "Eaton"],
    "BE": ["$BE", "Bloom Energy"],
    "CRWV": ["CRWV", "$CRWV", "CoreWeave"],
    "CORZ": ["CORZ", "$CORZ", "Core Scientific"],
    "CRDO": ["CRDO", "$CRDO", "Credo", "AEC"],
    "NVT": ["NVT", "$NVT", "nVent"],
    "FN": ["FN", "$FN", "Fabrinet"],
    "CLS": ["CLS", "$CLS", "Celestica"],
    "CEG": ["CEG", "$CEG", "Constellation Energy"],
    "VST": ["VST", "$VST", "Vistra"],
    "NRG": ["NRG", "$NRG", "NRG Energy"],
    "TLN": ["TLN", "$TLN", "Talen Energy"],
    "KGS": ["KGS", "$KGS", "Kodiak Gas"],
    "FLNC": ["FLNC", "$FLNC", "Fluence"],
    "DLR": ["DLR", "$DLR", "Digital Realty"],
    "EQIX": ["EQIX", "$EQIX", "Equinix"],
    "CIEN": ["CIEN", "$CIEN", "Ciena"],
    "MOD": ["MOD", "$MOD", "Modine"],
    "JCI": ["JCI", "$JCI", "Johnson Controls"],
    "CARR": ["CARR", "$CARR", "Carrier"],
    "HUBB": ["HUBB", "$HUBB", "Hubbell"],
    "GNRC": ["GNRC", "$GNRC", "Generac"],
    "APLD": ["APLD", "$APLD", "Applied Digital"],
    "IREN": ["IREN", "$IREN"],
    "CIFR": ["CIFR", "$CIFR", "Cipher Mining"],
    "CLSK": ["CLSK", "$CLSK", "CleanSpark"],
    "RIOT": ["RIOT", "$RIOT", "Riot Platforms"],
    "HUT": ["HUT", "$HUT", "Hut 8"],
    "BTDR": ["BTDR", "$BTDR", "Bitdeer"],
    "BITF": ["BITF", "$BITF", "Bitfarms"],
    "LITE": ["LITE", "$LITE", "Lumentum"],
    "COHR": ["COHR", "$COHR", "Coherent"],
    "EQT": ["EQT", "$EQT"],
    "LBRT": ["LBRT", "$LBRT", "Liberty Energy"],
    "PUMP": ["PUMP", "$PUMP", "ProPetro"],
    "PSIX": ["PSIX", "$PSIX", "Power Solutions International"],
}

THEME_PATTERNS: dict[str, list[str]] = {
    "AI基础设施": ["ai infrastructure", "ai capex", "data center", "datacenter", "power", "gpu cluster"],
    "半导体": ["semiconductor", "semis", "chip", "chips", "hbm", "gpu", "asic"],
    "云与AI软件": ["cloud", "aws", "azure", "google cloud", "enterprise software", "saas"],
    "估值/泡沫": ["bubble", "valuation", "valuations", "hype", "late to the", "crowded"],
    "期权/波动率": ["call", "put", "options", "iv", "volatility", "leaps", "calendar spread"],
    "宏观/利率": ["fed", "rate", "rates", "yield", "inflation", "rba", "factory orders"],
}

THEME_CANONICAL = {
    "AI infrastructure": "AI基础设施",
    "AI capex": "AI基础设施",
    "data center power": "AI基础设施",
    "semiconductor cycle": "半导体",
    "cloud spending": "云与AI软件",
    "enterprise software demand": "云与AI软件",
    "Fed rates": "宏观/利率",
}

CROWDING_WORDS = {
    "bubble",
    "hype",
    "crowded",
    "everyone",
    "late",
    "shorting",
    "valuation",
    "valuations",
    "reality check",
}

CATALYST_WORDS = {
    "earnings",
    "guidance",
    "upgrade",
    "downgrade",
    "launch",
    "product",
    "capex",
    "forecast",
    "calendar spread",
}

AMBIGUOUS_BARE_TICKERS = {"AI", "APP", "ARM", "BILL", "ON", "V", "NOW", "TEM"}


def _load(path: Path, default: Any) -> Any:
    try:
        return read_json(path, default)
    except (json.JSONDecodeError, OSError):
        return default


def _latest_json(folder: Path, pattern: str) -> tuple[dict[str, Any], Path | None]:
    files = sorted(folder.glob(pattern))
    if not files:
        return {}, None
    path = files[-1]
    return _load(path, {}), path


def _to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _age_hours(value: Any) -> float | None:
    dt = _to_dt(value)
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc).astimezone() - dt.astimezone()).total_seconds() / 3600, 2)


def _text(item: dict[str, Any]) -> str:
    return f"{item.get('title', '')} {item.get('summary', '')}"


def _contains_alias(text: str, alias: str) -> bool:
    if not alias:
        return False
    if alias.startswith("$"):
        return alias.lower() in text.lower()
    if alias.isupper() and len(alias) <= 5:
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, re.IGNORECASE) is not None
    return alias.lower() in text.lower()


def _symbol_aliases() -> dict[str, list[str]]:
    aliases = {symbol: list(values) for symbol, values in CORE_SYMBOL_ALIASES.items()}
    rules = _load(ROOT / "rule-engine.json", {})
    for symbol in rules.get("universe", []):
        symbol_text = str(symbol)
        aliases.setdefault(
            symbol_text,
            [f"${symbol_text}"] if symbol_text in AMBIGUOUS_BARE_TICKERS else [symbol_text, f"${symbol_text}"],
        )
    market = _load(ROOT / "data" / "market" / "latest.json", {})
    for row in market.get("watchSymbols", []) + market.get("indices", []) + market.get("sectorEtfs", []):
        symbol = row.get("symbol")
        if symbol:
            symbol_text = str(symbol)
            aliases.setdefault(
                symbol_text,
                [f"${symbol_text}"] if symbol_text in AMBIGUOUS_BARE_TICKERS else [symbol_text, f"${symbol_text}"],
            )
    portfolio = _load(ROOT / "data" / "trading" / "paper-portfolio.json", {})
    for row in portfolio.get("positions", []):
        symbol = row.get("symbol")
        if symbol:
            aliases.setdefault(str(symbol), [str(symbol), f"${symbol}"])
    return aliases


def _mentioned_symbols(item: dict[str, Any], aliases: dict[str, list[str]]) -> list[str]:
    text = _text(item)
    hits = set(item.get("symbol_hits", []) or [])
    hits.update(item.get("candidate_symbol_hits", []) or [])
    for symbol, symbol_aliases in aliases.items():
        if any(_contains_alias(text, alias) for alias in symbol_aliases):
            hits.add(symbol)
    return sorted(str(hit) for hit in hits)


def _theme_hits(item: dict[str, Any]) -> list[str]:
    text = _text(item).lower()
    hits = {THEME_CANONICAL.get(str(theme), str(theme)) for theme in item.get("theme_hits", []) or []}
    for theme, patterns in THEME_PATTERNS.items():
        if any(pattern in text for pattern in patterns):
            hits.add(theme)
    return sorted(hits)


def _label_from_score(score: float) -> tuple[str, str]:
    if score >= 0.25:
        return "POSITIVE", "偏正向"
    if score <= -0.25:
        return "NEGATIVE", "偏负面"
    return "NEUTRAL", "中性"


def _crowding_risk(mentions: int, net_score: float, crowding_hits: int) -> str:
    if mentions >= 5 and (abs(net_score) >= 0.45 or crowding_hits >= 2):
        return "HIGH"
    if mentions >= 3 or crowding_hits:
        return "MEDIUM"
    return "LOW"


def _source_quality(source_id: str) -> float:
    source_id = source_id.lower()
    if "fed" in source_id:
        return 1.0
    if "gdelt" in source_id or "news" in source_id:
        return 0.75
    if "reddit" in source_id:
        return 0.45
    if "manual" in source_id or "x" in source_id:
        return 0.55
    return 0.5


def _source_weight(item: dict[str, Any]) -> float:
    source_id = str(item.get("source_id") or "")
    priority = float(item.get("source_priority") or _source_quality(source_id))
    item_score = float(item.get("score") or 0)
    return max(0.25, min(2.0, priority + item_score / 10))


def _top_items(items: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    rows = []
    for item in items[:limit]:
        rows.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "sourceId": item.get("source_id"),
                "sourceLabel": item.get("source_label"),
                "publishedAt": item.get("published_at"),
                "sentimentLabel": item.get("sentiment_label"),
                "sentimentScore": item.get("sentiment_score"),
                "score": item.get("score"),
            }
        )
    return rows


def _diversify_event_radar(items: list[dict[str, Any]], max_per_cluster: int = 3) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    diversified: list[dict[str, Any]] = []
    for item in items:
        symbols = item.get("symbols") or item.get("symbol_hits") or item.get("candidate_symbol_hits") or []
        key = ",".join(sorted(str(symbol) for symbol in symbols)) or str(item.get("source_id") or "unknown")
        if counts.get(key, 0) >= max_per_cluster:
            continue
        counts[key] = counts.get(key, 0) + 1
        diversified.append(item)
    return diversified


def _event_priority(item: dict[str, Any]) -> float:
    priority = float(item.get("score") or 0)
    source = str(item.get("source_label") or item.get("source_id") or "").lower()
    title = str(item.get("title") or "")
    if "xiaohongshu" in source or "小红书" in title:
        priority += 12
    if "manual" in source or "opencli" in source:
        priority += 3
    return priority


def build_social_sentiment_result() -> dict[str, Any]:
    intel, intel_path = _latest_json(INTEL_DIR, "*_intel.json")
    items = list(intel.get("items", []) or [])
    aliases = _symbol_aliases()
    now = now_iso()
    source_age = _age_hours(intel.get("timestamp"))
    status = "PASS" if items else "MISSING"
    if source_age is not None and source_age > 6:
        status = "STALE"

    symbol_acc: dict[str, dict[str, Any]] = {}
    theme_acc: dict[str, dict[str, Any]] = {}
    crowding_items: list[dict[str, Any]] = []
    catalyst_items: list[dict[str, Any]] = []
    event_radar_items: list[dict[str, Any]] = []
    total_weight = 0.0
    weighted_sentiment = 0.0

    enriched_items = []
    for item in items:
        text = _text(item)
        sentiment = float(item.get("sentiment_score") or 0)
        weight = _source_weight(item)
        symbols = _mentioned_symbols(item, aliases)
        themes = _theme_hits(item)
        lower = text.lower()
        crowding_hit_count = sum(1 for word in CROWDING_WORDS if word in lower)
        catalyst_hit_count = sum(1 for word in CATALYST_WORDS if word in lower)
        enriched = {
            **item,
            "symbols": symbols,
            "themes": themes,
            "crowdingHitCount": crowding_hit_count,
            "catalystHitCount": catalyst_hit_count,
        }
        enriched_items.append(enriched)
        if item.get("event_hits"):
            event_radar_items.append(enriched)
        total_weight += weight
        weighted_sentiment += sentiment * weight
        if crowding_hit_count:
            crowding_items.append(enriched)
        if catalyst_hit_count:
            catalyst_items.append(enriched)

        for symbol in symbols:
            row = symbol_acc.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "mentionCount": 0,
                    "weightedMentions": 0.0,
                    "weightedSentiment": 0.0,
                    "positive": 0,
                    "neutral": 0,
                    "negative": 0,
                    "crowdingHits": 0,
                    "catalystHits": 0,
                    "sources": set(),
                    "items": [],
                },
            )
            row["mentionCount"] += 1
            row["weightedMentions"] += weight
            row["weightedSentiment"] += sentiment * weight
            row["crowdingHits"] += crowding_hit_count
            row["catalystHits"] += catalyst_hit_count
            label = str(item.get("sentiment_label") or "NEUTRAL").lower()
            row[label if label in {"positive", "neutral", "negative"} else "neutral"] += 1
            row["sources"].add(item.get("source_label") or item.get("source_id") or "unknown")
            row["items"].append(enriched)

        for theme in themes:
            row = theme_acc.setdefault(
                theme,
                {
                    "theme": theme,
                    "mentionCount": 0,
                    "weightedSentiment": 0.0,
                    "weightedMentions": 0.0,
                    "items": [],
                },
            )
            row["mentionCount"] += 1
            row["weightedMentions"] += weight
            row["weightedSentiment"] += sentiment * weight
            row["items"].append(enriched)

    symbol_signals = []
    for row in symbol_acc.values():
        net_score = row["weightedSentiment"] / row["weightedMentions"] if row["weightedMentions"] else 0.0
        label, label_zh = _label_from_score(net_score)
        symbol_signals.append(
            {
                "symbol": row["symbol"],
                "mentionCount": row["mentionCount"],
                "weightedMentions": round(row["weightedMentions"], 2),
                "netSentiment": round(net_score, 3),
                "sentimentLabel": label,
                "sentimentLabelZh": label_zh,
                "positive": row["positive"],
                "neutral": row["neutral"],
                "negative": row["negative"],
                "crowdingRisk": _crowding_risk(row["mentionCount"], net_score, row["crowdingHits"]),
                "crowdingHits": row["crowdingHits"],
                "catalystHits": row["catalystHits"],
                "topSources": sorted(row["sources"])[:4],
                "topItems": _top_items(sorted(row["items"], key=lambda item: item.get("score", 0), reverse=True), 3),
                "decisionUseZh": "只作为仓位置信度/风险拥挤度 overlay；不能单独触发买卖。",
            }
        )
    symbol_signals.sort(key=lambda row: (row["mentionCount"], abs(row["netSentiment"])), reverse=True)

    theme_signals = []
    for row in theme_acc.values():
        net_score = row["weightedSentiment"] / row["weightedMentions"] if row["weightedMentions"] else 0.0
        label, label_zh = _label_from_score(net_score)
        theme_signals.append(
            {
                "theme": row["theme"],
                "mentionCount": row["mentionCount"],
                "netSentiment": round(net_score, 3),
                "sentimentLabel": label,
                "sentimentLabelZh": label_zh,
                "topItems": _top_items(sorted(row["items"], key=lambda item: item.get("score", 0), reverse=True), 2),
            }
        )
    theme_signals.sort(key=lambda row: row["mentionCount"], reverse=True)

    market_net = weighted_sentiment / total_weight if total_weight else 0.0
    market_label, market_label_zh = _label_from_score(market_net)
    total_items = len(items)
    crowding_risk = _crowding_risk(total_items, market_net, len(crowding_items))
    confidence = "HIGH" if total_items >= 35 else "MEDIUM" if total_items >= 12 else "LOW"
    if status != "PASS":
        confidence = "LOW"

    disagreement_risks = [
        {
            "title": item.get("title"),
            "url": item.get("url"),
            "sourceLabel": item.get("source_label"),
            "publishedAt": item.get("published_at"),
            "reasonZh": "出现泡沫、拥挤、估值或反身性相关措辞，需要降低追涨冲动。",
        }
        for item in sorted(crowding_items, key=lambda item: item.get("score", 0), reverse=True)[:6]
    ]

    catalyst_watch = [
        {
            "title": item.get("title"),
            "url": item.get("url"),
            "sourceLabel": item.get("source_label"),
            "publishedAt": item.get("published_at"),
            "symbols": item.get("symbols", []),
            "themes": item.get("themes", []),
        }
        for item in sorted(catalyst_items, key=lambda item: item.get("score", 0), reverse=True)[:6]
    ]

    diversified_event_radar_items = _diversify_event_radar(
        sorted(event_radar_items, key=_event_priority, reverse=True)
    )
    event_radar = [
        {
            "title": item.get("title"),
            "url": item.get("url"),
            "sourceLabel": item.get("source_label"),
            "publishedAt": item.get("published_at"),
            "symbols": item.get("symbols", []),
            "themes": item.get("themes", []),
            "eventTypes": item.get("event_hits", []),
            "sentimentLabel": item.get("sentiment_label"),
            "sentimentScore": item.get("sentiment_score"),
            "score": item.get("score"),
        }
        for item in diversified_event_radar_items[:12]
    ]

    return {
        "task": "social_sentiment_feed",
        "timestamp": now,
        "status": status,
        "source": "intel_monitor derived feed + daily-us-market-sentiment-brief cron",
        "sourceRun": {
            "intelPath": str(intel_path.relative_to(ROOT)) if intel_path else None,
            "intelTimestamp": intel.get("timestamp"),
            "ageHours": source_age,
            "itemCount": total_items,
            "highlightCount": len(intel.get("highlights", []) or []),
            "sourceCount": intel.get("source_count"),
            "sourceErrors": intel.get("source_errors", {}),
            "xApiReady": intel.get("x_api_ready"),
            "xApiNote": intel.get("x_api_note"),
            "xKolCount": intel.get("x_kol_count"),
            "xFetchedItemCount": intel.get("x_fetched_item_count"),
        },
        "marketMood": {
            "label": market_label,
            "labelZh": market_label_zh,
            "score": round(market_net, 3),
            "confidence": confidence,
            "crowdingRisk": crowding_risk,
            "freshnessZh": "新鲜" if status == "PASS" else "舆情源偏旧或缺失",
            "useInDecisionZh": "把社媒舆情作为最大 15% 的置信度/拥挤度 overlay；不能覆盖宏观、财报、仓位和人工批准。",
        },
        "sentimentSummary": {
            "positive": sum(1 for item in items if item.get("sentiment_label") == "POSITIVE"),
            "neutral": sum(1 for item in items if item.get("sentiment_label") == "NEUTRAL"),
            "negative": sum(1 for item in items if item.get("sentiment_label") == "NEGATIVE"),
            "total": total_items,
            "netScore": round(market_net, 3),
        },
        "symbolSignals": symbol_signals[:18],
        "themeSignals": theme_signals[:10],
        "disagreementRisks": disagreement_risks,
        "catalystWatch": catalyst_watch,
        "eventRadar": event_radar,
        "topItems": _top_items(sorted(enriched_items, key=lambda item: item.get("score", 0), reverse=True), 10),
        "judgementModel": {
            "maxWeight": 0.15,
            "buyGate": "Social sentiment can only add confidence after price, macro, event-risk, sizing, and user approval pass.",
            "sellOrReduceGate": "High crowding or negative narrative can reduce sizing or demand a tighter invalidation, but should be cross-checked with price and event data.",
            "optionsGate": "Social option chatter never overrides earnings blackout or volatility-risk rules.",
        },
        "dashboard": {
            "sectionId": "sentiment",
            "sourceNoteZh": "社媒信号来自本地 daily sentiment/intel 管线；X KOL 通过 OpenCLI Browser Bridge 读取，XHS/其他来源仍可用人工链接队列补充。",
            "refreshCadenceZh": "每日 20:45 北京时间预盘舆情刷新；盘中看板会读取最新本地快照。",
        },
        "assumptions": {
            "execution": "research-only; no broker order, no real-account read",
            "noisePolicy": "Social posts are noisy and can be brigaded, stale, or wrong; treat them as narrative and crowding evidence.",
        },
    }


def write_outputs(result: dict[str, Any]) -> tuple[Path, Path, Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    latest_path = DATA_DIR / "latest.json"
    snapshot_path = DATA_DIR / f"{today_stamp()}_social_sentiment.json"
    report_path = REPORTS_DIR / f"{today_stamp()}_social_sentiment_feed.md"
    write_json(latest_path, result)
    write_json(snapshot_path, result)

    mood = result.get("marketMood", {})
    lines = [
        "# 社媒舆情 Feed",
        "",
        f"- 时间: {result['timestamp']}",
        f"- 状态: {result['status']}",
        f"- 市场情绪: {mood.get('labelZh')} ({mood.get('score')})",
        f"- 拥挤风险: {mood.get('crowdingRisk')}",
        f"- 使用方式: {mood.get('useInDecisionZh')}",
        "",
        "## 重点标的",
        "",
    ]
    for row in result.get("symbolSignals", [])[:8]:
        lines.append(
            f"- {row['symbol']}: {row['sentimentLabelZh']} / mentions={row['mentionCount']} / "
            f"net={row['netSentiment']} / crowding={row['crowdingRisk']}"
        )
    if not result.get("symbolSignals"):
        lines.append("- 暂无标的级舆情信号。")

    lines += ["", "## 主题", ""]
    for row in result.get("themeSignals", [])[:6]:
        lines.append(f"- {row['theme']}: {row['sentimentLabelZh']} / mentions={row['mentionCount']} / net={row['netSentiment']}")
    if not result.get("themeSignals"):
        lines.append("- 暂无主题级舆情信号。")

    lines += ["", "## 分歧/拥挤风险", ""]
    for row in result.get("disagreementRisks", [])[:5]:
        lines.append(f"- {row.get('title')} ({row.get('sourceLabel')}) {row.get('url') or ''}".rstrip())
    if not result.get("disagreementRisks"):
        lines.append("- 暂无明显拥挤/分歧提示。")

    lines += [
        "",
        "## 安全边界",
        "",
        "- 舆情只作为研究 overlay，不会触发券商订单。",
        "- 任何 paper trade 仍需通过宏观、财报、仓位、人工批准和本地 guard。",
    ]
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    append_trade_log(
        {
            "timestamp": result["timestamp"],
            "task": "social_sentiment_feed",
            "status": result["status"],
            "summary": f"Social sentiment feed: {mood.get('labelZh')} / crowding {mood.get('crowdingRisk')}.",
            "report": str(report_path.relative_to(ROOT)),
            "proposals": [
                {
                    "type": "social_sentiment_symbol",
                    "symbol": row.get("symbol"),
                    "netSentiment": row.get("netSentiment"),
                    "crowdingRisk": row.get("crowdingRisk"),
                }
                for row in result.get("symbolSignals", [])[:8]
            ],
        }
    )
    return report_path, latest_path, snapshot_path


def main() -> int:
    result = build_social_sentiment_result()
    report, latest, snapshot = write_outputs(result)
    print(
        json.dumps(
            {"task": result["task"], "status": result["status"], "report": str(report), "latest": str(latest), "snapshot": str(snapshot)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
