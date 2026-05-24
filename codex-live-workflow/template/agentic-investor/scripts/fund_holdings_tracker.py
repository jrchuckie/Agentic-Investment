from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from agentic_investor_common import REPORTS_DIR, TRADE_LOG_PATH, now_iso, read_json, today_stamp, write_json


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "fund-managers.json"
DATA_DIR = ROOT / "data" / "fund_holdings"
LATEST_PATH = DATA_DIR / "latest.json"


TECH_TICKER_WHITELIST = {
    "AAPL", "ADBE", "ALAB", "AMD", "AMAT", "AMZN", "ANET", "APLD", "APP", "ARM",
    "ASML", "AVGO", "BE", "BILL", "BITF", "BTDR", "CEG", "CIFR", "CLS", "CLSK",
    "COHR", "COIN", "CORZ", "CRCL", "CRM", "CRWD", "CRWV", "DDOG", "DELL", "EQT",
    "FROG", "GOOG", "GOOGL", "GTLB", "HOOD", "HUT", "INTU", "IREN", "LITE", "LRCX",
    "META", "MRVL", "MSFT", "MU", "NBIS", "NFLX", "NOW", "NVDA", "OKTA", "ORCL",
    "PLTR", "PSIX", "RDDT", "RIOT", "ROKU", "SHOP", "SMCI", "SMH", "SNOW", "SPOT",
    "TEM", "TER", "TSLA", "TSM", "TSEM", "UBER", "VRT", "VST", "WDC",
}

TECH_KEYWORDS = (
    "ai", "artificial intelligence", "semiconductor", "software", "cloud", "data",
    "internet", "platform", "cyber", "digital", "robot", "automation", "chip",
    "technology", "systems", "comput", "network", "fintech", "crypto", "power",
    "energy", "infrastructure", "data center", "datacenter", "fiber", "optical",
    "mining",
)

SYMBOL_EXCLUDES = {
    "", "-", "--", "N/A", "NA", "NAN", "CASH", "OTHER", "OTHER ASSETS AND LIABILITIES",
    "USD", "EUR", "JPY",
}


@dataclass(frozen=True)
class Holding:
    source_id: str
    manager: str
    firm: str
    vehicle: str
    vehicle_symbol: str | None
    source_type: str
    disclosure_frequency: str
    as_of: str | None
    filing_date: str | None
    symbol: str | None
    name: str
    cusip: str | None
    shares: float | None
    market_value: float | None
    weight_pct: float | None
    source_url: str | None
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "manager": self.manager,
            "firm": self.firm,
            "vehicle": self.vehicle,
            "vehicle_symbol": self.vehicle_symbol,
            "source_type": self.source_type,
            "disclosure_frequency": self.disclosure_frequency,
            "as_of": self.as_of,
            "filing_date": self.filing_date,
            "symbol": self.symbol,
            "name": self.name,
            "cusip": self.cusip,
            "shares": self.shares,
            "market_value": self.market_value,
            "weight_pct": self.weight_pct,
            "source_url": self.source_url,
            "raw": self.raw,
        }


def _read_config() -> dict[str, Any]:
    return read_json(CONFIG_PATH)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\ufeff", "").strip()


def _float(value: Any) -> float | None:
    text = _clean_text(value)
    if not text or text.upper() in SYMBOL_EXCLUDES:
        return None
    text = text.replace("$", "").replace("%", "").replace(",", "").replace("(", "-").replace(")", "")
    try:
        return float(text)
    except ValueError:
        return None


def _first_value(row: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    normalized = {str(key).strip().lower(): value for key, value in row.items()}
    for candidate in candidates:
        if candidate.lower() in normalized:
            return normalized[candidate.lower()]
    return None


def _normalize_symbol(value: Any, aliases: dict[str, str | None]) -> str | None:
    raw = _clean_text(value).upper()
    if raw in aliases:
        mapped = aliases[raw]
        return mapped.upper() if mapped else None
    if raw in SYMBOL_EXCLUDES:
        return None
    if raw.startswith("NASDAQ: "):
        raw = raw.split(": ", 1)[1]
    if ":" in raw:
        mapped = aliases.get(raw)
        return mapped.upper() if mapped else None
    raw = raw.replace(" ", "")
    if raw in SYMBOL_EXCLUDES:
        return None
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,7}", raw):
        return None
    if raw.startswith("B.0") or raw.startswith("US"):
        return None
    return raw


def _is_backtest_symbol(symbol: str | None) -> bool:
    if not symbol:
        return False
    if symbol in SYMBOL_EXCLUDES:
        return False
    return bool(re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,7}", symbol))


def _is_backtest_holding(holding: Holding) -> bool:
    if not _is_backtest_symbol(holding.symbol):
        return False
    raw = {str(key).strip().lower(): _clean_text(value).upper() for key, value in holding.raw.items()}
    asset_class = raw.get("asset class", "")
    sector = raw.get("sector", "")
    exchange = raw.get("exchange", "")
    name = holding.name.upper()
    if asset_class and asset_class not in {"EQUITY", "STOCK"}:
        return False
    if "CASH" in sector or "DERIVATIVE" in sector:
        return False
    if "NO MARKET" in exchange or "UNLISTED" in exchange:
        return False
    if "PRVT" in name or "PREF EQ" in name:
        return False
    return True


def _request_headers(sec: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml,text/csv,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
        "User-Agent": "agentic-investor-holdings-tracker/1.0",
    }
    if sec:
        headers["User-Agent"] = os.environ.get(
            "SEC_USER_AGENT",
            "agentic-investor research contact@example.com",
        )
    return headers


def _fetch_text(url: str, timeout: int, sec: bool = False) -> str:
    request = urllib.request.Request(url, headers=_request_headers(sec=sec))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _csv_records(text: str) -> list[dict[str, Any]]:
    lines = [line for line in text.splitlines() if line.strip()]
    header_idx = 0
    for idx, line in enumerate(lines):
        lower = line.lower()
        if "ticker" in lower and ("weight" in lower or "market value" in lower or "company" in lower):
            header_idx = idx
            break
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    return [dict(row) for row in reader if any(_clean_text(value) for value in row.values())]


def _csv_as_of(text: str) -> str | None:
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        cells = [_clean_text(cell) for cell in row]
        lower = [cell.lower() for cell in cells]
        for idx, cell in enumerate(lower):
            if "holdings as of" in cell or cell == "as of":
                for candidate in cells[idx + 1:]:
                    if candidate:
                        return candidate
    return None


def _holding_from_row(source: dict[str, Any], row: dict[str, Any], aliases: dict[str, str | None], as_of: str | None = None) -> Holding | None:
    symbol = _normalize_symbol(
        _first_value(row, ("ticker", "symbol", "holding ticker", "no.symbol", "no. symbol")),
        aliases,
    )
    name = _clean_text(_first_value(row, ("name", "company", "issuer name", "security name", "holding name")))
    cusip = _clean_text(_first_value(row, ("cusip", "cusip number"))) or None
    shares = _float(_first_value(row, ("shares", "quantity", "shares held", "shares/principal", "sshprnamt")))
    market_value = _float(_first_value(row, ("market value", "market value($)", "market value ($)", "value", "notional value")))
    weight = _float(_first_value(row, ("weight (%)", "weight %", "% weight", "weight", "%")))
    if not symbol and cusip:
        symbol = _normalize_symbol(source.get("_cusip_symbol_map", {}).get(cusip.upper()), aliases)
    if not symbol and not name:
        return None
    return Holding(
        source_id=source["id"],
        manager=source.get("manager", ""),
        firm=source.get("firm", ""),
        vehicle=source.get("vehicle", ""),
        vehicle_symbol=source.get("symbol"),
        source_type=source.get("source_type", ""),
        disclosure_frequency=source.get("disclosure_frequency", ""),
        as_of=as_of,
        filing_date=None,
        symbol=symbol,
        name=name,
        cusip=cusip,
        shares=shares,
        market_value=market_value,
        weight_pct=weight,
        source_url=source.get("url"),
        raw=row,
    )


def _parse_csv_source(source: dict[str, Any], text: str, aliases: dict[str, str | None]) -> list[Holding]:
    records = _csv_records(text)
    as_of = _csv_as_of(text)
    holdings: list[Holding] = []
    for row in records:
        row_as_of = _clean_text(_first_value(row, ("date", "as of", "as_of"))) or as_of
        holding = _holding_from_row(source, row, aliases, as_of=row_as_of)
        if holding:
            holdings.append(holding)
    return holdings


def _flatten_columns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [
        " ".join(str(part).strip() for part in column if str(part).strip() and str(part).strip() != "nan")
        if isinstance(column, tuple)
        else str(column).strip()
        for column in frame.columns
    ]
    return frame


def _parse_stockanalysis_source(source: dict[str, Any], text: str, aliases: dict[str, str | None]) -> list[Holding]:
    frames = pd.read_html(io.StringIO(text))
    chosen: pd.DataFrame | None = None
    for frame in frames:
        frame = _flatten_columns(frame)
        columns = {column.lower() for column in frame.columns}
        has_symbol = any("symbol" in column or "ticker" in column for column in columns)
        has_weight = any("weight" in column or "%" == column for column in columns)
        if has_symbol and has_weight:
            chosen = frame
            break
    if chosen is None:
        raise RuntimeError("No holdings table found in StockAnalysis HTML.")

    as_of = None
    match = re.search(r"As of ([A-Z][a-z]{2} \d{1,2}, \d{4})", text)
    if match:
        as_of = match.group(1)

    holdings: list[Holding] = []
    for row in chosen.to_dict(orient="records"):
        normalized_row = {str(key).replace("\n", " ").strip(): value for key, value in row.items()}
        holding = _holding_from_row(source, normalized_row, aliases, as_of=as_of)
        if holding:
            holdings.append(holding)
    return holdings


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(node: ET.Element, name: str) -> str:
    for child in list(node):
        if _strip_ns(child.tag).lower() == name.lower():
            return _clean_text(child.text)
    return ""


def _latest_13f_metadata(cik: str, timeout: int) -> dict[str, Any]:
    cik_padded = f"{int(cik):010d}"
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    data = json.loads(_fetch_text(url, timeout=timeout, sec=True))
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    primary_docs = recent.get("primaryDocument", [])
    for idx, form in enumerate(forms):
        if form in {"13F-HR", "13F-HR/A"}:
            accession = accessions[idx]
            return {
                "cik": cik,
                "cik_int": str(int(cik)),
                "accession": accession,
                "accession_nodash": accession.replace("-", ""),
                "filing_date": filing_dates[idx] if idx < len(filing_dates) else None,
                "report_date": report_dates[idx] if idx < len(report_dates) else None,
                "primary_document": primary_docs[idx] if idx < len(primary_docs) else None,
            }
    raise RuntimeError(f"No recent 13F-HR filing found for CIK {cik}.")


def _latest_13f_xml(meta: dict[str, Any], timeout: int) -> tuple[str, str]:
    base = f"https://www.sec.gov/Archives/edgar/data/{meta['cik_int']}/{meta['accession_nodash']}"
    index = json.loads(_fetch_text(f"{base}/index.json", timeout=timeout, sec=True))
    items = index.get("directory", {}).get("item", [])
    names = [item.get("name", "") for item in items]
    preferred = [
        name for name in names
        if name.lower().endswith(".xml") and ("info" in name.lower() or "13f" in name.lower())
    ]
    fallback = [name for name in names if name.lower().endswith(".xml")]
    for name in preferred + fallback:
        xml_url = f"{base}/{name}"
        xml_text = _fetch_text(xml_url, timeout=timeout, sec=True)
        if "infoTable" in xml_text or "informationTable" in xml_text:
            return xml_text, xml_url
    raise RuntimeError("No 13F information table XML found in SEC archive index.")


def _parse_sec_13f_source(source: dict[str, Any], config: dict[str, Any], aliases: dict[str, str | None], timeout: int) -> list[Holding]:
    source = dict(source)
    source["_cusip_symbol_map"] = config.get("cusip_symbol_map", {})
    meta = _latest_13f_metadata(str(source["cik"]), timeout)
    xml_text, xml_url = _latest_13f_xml(meta, timeout)
    root = ET.fromstring(xml_text.encode("utf-8"))
    tables = [node for node in root.iter() if _strip_ns(node.tag) == "infoTable"]
    raw_rows: list[dict[str, Any]] = []
    total_value = 0.0
    for table in tables:
        row = {
            "issuer name": _child_text(table, "nameOfIssuer"),
            "title": _child_text(table, "titleOfClass"),
            "cusip": _child_text(table, "cusip"),
            "value": _child_text(table, "value"),
            "sshPrnamt": _child_text(table, "sshPrnamt"),
            "sshPrnamtType": _child_text(table, "sshPrnamtType"),
            "putCall": _child_text(table, "putCall"),
        }
        value = _float(row["value"]) or 0.0
        total_value += value
        raw_rows.append(row)

    holdings: list[Holding] = []
    for row in raw_rows:
        value = _float(row["value"])
        row["weight"] = (value / total_value * 100) if value is not None and total_value else None
        holding = _holding_from_row(source, row, aliases, as_of=meta.get("report_date"))
        if holding:
            holdings.append(
                Holding(
                    **{
                        **holding.to_dict(),
                        "filing_date": meta.get("filing_date"),
                        "source_url": xml_url,
                    }
                )
            )
    return holdings


def _holding_from_dict(row: dict[str, Any]) -> Holding:
    return Holding(
        source_id=row.get("source_id", ""),
        manager=row.get("manager", ""),
        firm=row.get("firm", ""),
        vehicle=row.get("vehicle", ""),
        vehicle_symbol=row.get("vehicle_symbol"),
        source_type=row.get("source_type", ""),
        disclosure_frequency=row.get("disclosure_frequency", ""),
        as_of=row.get("as_of"),
        filing_date=row.get("filing_date"),
        symbol=row.get("symbol"),
        name=row.get("name", ""),
        cusip=row.get("cusip"),
        shares=row.get("shares"),
        market_value=row.get("market_value"),
        weight_pct=row.get("weight_pct"),
        source_url=row.get("source_url"),
        raw=row.get("raw", {}),
    )


def _parse_seed_holdings_source(source: dict[str, Any], config: dict[str, Any], aliases: dict[str, str | None]) -> list[Holding]:
    source = dict(source)
    source["_cusip_symbol_map"] = config.get("cusip_symbol_map", {})
    rows = list(source.get("seed_holdings", []))
    total_value = sum(_float(row.get("value")) or 0.0 for row in rows)
    holdings: list[Holding] = []
    for row in rows:
        seeded = dict(row)
        value = _float(seeded.get("value"))
        seeded["weight"] = (value / total_value * 100) if value is not None and total_value else None
        holding = _holding_from_row(source, seeded, aliases, as_of=source.get("seed_as_of"))
        if holding:
            holdings.append(
                Holding(
                    **{
                        **holding.to_dict(),
                        "filing_date": source.get("seed_filing_date"),
                        "source_url": source.get("seed_source_url") or source.get("url"),
                    }
                )
            )
    return holdings


def fetch_source(source: dict[str, Any], config: dict[str, Any], timeout: int) -> tuple[list[Holding], dict[str, Any]]:
    aliases = {key.upper(): value for key, value in config.get("symbol_aliases", {}).items()}
    source_type = source.get("source_type")
    status = "ok"
    fresh_error = None
    try:
        if source_type == "sec_13f":
            holdings = _parse_sec_13f_source(source, config, aliases, timeout)
        else:
            text = _fetch_text(source["url"], timeout=timeout, sec=False)
            if source_type in {"ark_csv", "ishares_csv"}:
                holdings = _parse_csv_source(source, text, aliases)
            elif source_type == "stockanalysis_html":
                holdings = _parse_stockanalysis_source(source, text, aliases)
            else:
                raise RuntimeError(f"Unsupported source_type: {source_type}")
    except Exception as exc:
        if not source.get("seed_holdings"):
            raise
        holdings = _parse_seed_holdings_source(source, config, aliases)
        status = "seeded_fallback"
        fresh_error = str(exc)
    meta = {
        "id": source["id"],
        "manager": source.get("manager"),
        "firm": source.get("firm"),
        "vehicle": source.get("vehicle"),
        "source_type": source_type,
        "rows": len(holdings),
        "status": status,
    }
    if fresh_error:
        meta["fresh_error"] = fresh_error
    return holdings, meta


def _tech_score(symbol: str, name: str) -> float:
    if symbol in TECH_TICKER_WHITELIST:
        return 1.0
    lower_name = name.lower()
    if any(keyword in lower_name for keyword in TECH_KEYWORDS):
        return 0.8
    return 0.35


def aggregate_holdings(holdings: list[Holding], config: dict[str, Any]) -> list[dict[str, Any]]:
    sources = {source["id"]: source for source in config.get("sources", [])}
    by_symbol: dict[str, dict[str, Any]] = {}
    for holding in holdings:
        symbol = holding.symbol
        if not _is_backtest_holding(holding):
            continue
        source = sources.get(holding.source_id, {})
        weight_pct = holding.weight_pct if holding.weight_pct is not None else 0.0
        if weight_pct <= 0:
            continue
        manager_quality = float(source.get("quality_weight", 0.5))
        focus = float(source.get("tech_focus_weight", 0.5))
        tech = _tech_score(symbol, holding.name)
        score = (weight_pct / 100.0) * manager_quality * focus * tech
        bucket = by_symbol.setdefault(
            symbol,
            {
                "symbol": symbol,
                "name": holding.name,
                "score": 0.0,
                "manager_count": 0,
                "total_weight_pct": 0.0,
                "weighted_sources": [],
                "source_ids": set(),
            },
        )
        bucket["score"] += score
        bucket["total_weight_pct"] += weight_pct
        bucket["source_ids"].add(holding.source_id)
        bucket["weighted_sources"].append(
            {
                "source_id": holding.source_id,
                "manager": holding.manager,
                "vehicle": holding.vehicle,
                "weight_pct": weight_pct,
                "score_contribution": score,
            }
        )
    rows = []
    for bucket in by_symbol.values():
        bucket["manager_count"] = len(bucket["source_ids"])
        bucket["source_ids"] = sorted(bucket["source_ids"])
        bucket["weighted_sources"].sort(key=lambda item: item["score_contribution"], reverse=True)
        rows.append(bucket)
    rows.sort(key=lambda item: (item["score"], item["manager_count"], item["total_weight_pct"]), reverse=True)
    return rows


def _load_stale_latest() -> dict[str, Any] | None:
    candidates: list[Path] = []
    if LATEST_PATH.exists():
        candidates.append(LATEST_PATH)
    snapshot_dir = DATA_DIR / "snapshots"
    if snapshot_dir.exists():
        candidates.extend(sorted(snapshot_dir.glob("*_fund_holdings_tracker.json"), key=lambda path: path.stat().st_mtime, reverse=True))
    for path in candidates:
        try:
            data = read_json(path)
        except Exception:
            continue
        if data.get("holdings"):
            data["_stale_path"] = str(path.relative_to(ROOT))
            return data
    return None


def _stale_holdings_by_source(stale: dict[str, Any] | None) -> dict[str, list[Holding]]:
    by_source: dict[str, list[Holding]] = {}
    if not stale:
        return by_source
    for row in stale.get("holdings", []):
        holding = _holding_from_dict(row)
        by_source.setdefault(holding.source_id, []).append(holding)
    return by_source


def build_tracker_result(args: argparse.Namespace) -> dict[str, Any]:
    config = _read_config()
    timeout = int(config.get("settings", {}).get("default_request_timeout_seconds", 30))
    if args.timeout:
        timeout = args.timeout

    source_filter = {item.lower() for item in args.source}
    enabled_sources = [
        source for source in config.get("sources", [])
        if source.get("enabled", True) and (not source_filter or source["id"].lower() in source_filter)
    ]

    all_holdings: list[Holding] = []
    source_meta: dict[str, Any] = {}
    errors: dict[str, str] = {}
    stale = _load_stale_latest() if args.allow_stale else None
    stale_by_source = _stale_holdings_by_source(stale)
    for source in enabled_sources:
        try:
            holdings, meta = fetch_source(source, config, timeout)
            all_holdings.extend(holdings)
            source_meta[source["id"]] = meta
        except Exception as exc:
            stale_holdings = stale_by_source.get(source["id"], [])
            if stale_holdings:
                all_holdings.extend(stale_holdings)
                source_meta[source["id"]] = {
                    "id": source["id"],
                    "manager": source.get("manager"),
                    "firm": source.get("firm"),
                    "vehicle": source.get("vehicle"),
                    "source_type": source.get("source_type"),
                    "rows": len(stale_holdings),
                    "status": "stale_cache",
                    "fresh_error": str(exc),
                    "stale_timestamp": stale.get("timestamp") if stale else None,
                    "stale_path": stale.get("_stale_path") if stale else None,
                }
            else:
                errors[source["id"]] = str(exc)
                source_meta[source["id"]] = {
                    "id": source["id"],
                    "manager": source.get("manager"),
                    "firm": source.get("firm"),
                    "vehicle": source.get("vehicle"),
                    "source_type": source.get("source_type"),
                    "rows": 0,
                    "status": "error",
                    "error": str(exc),
                }

    aggregate = aggregate_holdings(all_holdings, config)
    max_symbols = args.max_backtest_symbols or int(config.get("settings", {}).get("max_backtest_symbols", 30))
    min_score = float(config.get("settings", {}).get("min_backtest_score", 0.0))
    candidate_rows = [row for row in aggregate if row["score"] >= min_score][:max_symbols]
    candidate_symbols = [row["symbol"] for row in candidate_rows]
    symbol_scores = {row["symbol"]: row["score"] for row in candidate_rows}
    max_score = max(symbol_scores.values(), default=0.0)
    normalized_scores = {
        symbol: (score / max_score if max_score else 0.0)
        for symbol, score in symbol_scores.items()
    }

    timestamp = now_iso()
    result = {
        "task": "fund_holdings_tracker",
        "timestamp": timestamp,
        "config_path": str(CONFIG_PATH.relative_to(ROOT)),
        "source_count": len(enabled_sources),
        "successful_source_count": sum(
            1 for meta in source_meta.values()
            if meta.get("status") in {"ok", "seeded_fallback", "stale_cache"}
        ),
        "error_count": len(errors),
        "sources": source_meta,
        "errors": errors,
        "holdings": [holding.to_dict() for holding in all_holdings],
        "aggregate": aggregate,
        "backtest_feed": {
            "generated_at": timestamp,
            "candidate_symbols": candidate_symbols,
            "symbol_scores": symbol_scores,
            "normalized_symbol_scores": normalized_scores,
            "score_method": "sum(position_weight_pct * manager_quality * source_tech_focus * ticker_tech_score)",
            "max_symbols": max_symbols,
            "min_score": min_score,
        },
        "assumptions": {
            "execution": "advisory-only; no account query and no orders",
            "13f_lag": "13F positions can be up to 45 days stale at publication and can omit shorts, non-US local lines, and many private holdings.",
            "mutual_fund_lag": "Mutual fund holdings can be delayed and may be top-holdings-only depending on source availability.",
            "backtest_warning": "Using the latest holdings feed in a historical backtest is an idea-generation overlay, not a clean point-in-time simulation until enough snapshots accumulate.",
        },
    }

    if not all_holdings and args.allow_stale:
        stale = _load_stale_latest()
        if stale:
            stale["stale_reused_at"] = timestamp
            stale["stale_reason"] = "Fresh tracker fetch returned no holdings."
            return stale
    return result


def write_outputs(result: dict[str, Any]) -> tuple[Path, Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    snapshot_path = DATA_DIR / "snapshots" / f"{stamp}_fund_holdings_tracker.json"
    latest_path = LATEST_PATH
    write_json(snapshot_path, result)
    write_json(latest_path, result)

    report_path = REPORTS_DIR / f"{today_stamp()}_fund_holdings_tracker.md"
    feed = result.get("backtest_feed", {})
    aggregate = result.get("aggregate", [])
    lines = [
        "# Fund Holdings Tracker",
        "",
        f"- Timestamp: {result.get('timestamp')}",
        f"- Sources: {result.get('successful_source_count', 0)}/{result.get('source_count', 0)} successful",
        f"- Holdings parsed: {len(result.get('holdings', []))}",
        f"- Backtest feed symbols: {', '.join(feed.get('candidate_symbols', []))}",
        f"- Latest cache: {latest_path.relative_to(ROOT)}",
        f"- Snapshot: {snapshot_path.relative_to(ROOT)}",
        "",
        "## Source Status",
        "",
        "| Source | Manager | Vehicle | Type | Rows | Status |",
        "|---|---|---|---|---:|---|",
    ]
    for source_id, meta in sorted(result.get("sources", {}).items()):
        lines.append(
            "| {source} | {manager} | {vehicle} | {type} | {rows} | {status} |".format(
                source=source_id,
                manager=meta.get("manager") or "",
                vehicle=meta.get("vehicle") or "",
                type=meta.get("source_type") or "",
                rows=meta.get("rows", 0),
                status=meta.get("status", ""),
            )
        )
    lines += [
        "",
        "## Top Aggregated Signals",
        "",
        "| Symbol | Score | Managers | Total Source Weight | Top Source |",
        "|---|---:|---:|---:|---|",
    ]
    for row in aggregate[:25]:
        top_source = row.get("weighted_sources", [{}])[0]
        lines.append(
            "| {symbol} | {score:.4f} | {count} | {weight:.2f}% | {source} {w:.2f}% |".format(
                symbol=row["symbol"],
                score=float(row["score"]),
                count=int(row["manager_count"]),
                weight=float(row["total_weight_pct"]),
                source=top_source.get("source_id", ""),
                w=float(top_source.get("weight_pct", 0.0)),
            )
        )
    if result.get("errors"):
        lines += ["", "## Fetch Errors", ""]
        for source_id, error in sorted(result["errors"].items()):
            lines.append(f"- {source_id}: {error}")
    lines += ["", "## Assumptions", ""]
    for key, value in result.get("assumptions", {}).items():
        lines.append(f"- {key}: {value}")
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    log = read_json(TRADE_LOG_PATH, {"records": []})
    log.setdefault("records", []).append(
        {
            "timestamp": result.get("timestamp"),
            "task": "fund_holdings_tracker",
            "status": "completed" if result.get("successful_source_count", 0) else "data_unavailable",
            "summary": f"Parsed {len(result.get('holdings', []))} disclosed holdings from {result.get('successful_source_count', 0)} sources.",
            "report": str(report_path.relative_to(ROOT)),
            "proposals": [
                {
                    "type": "backtest_feed",
                    "candidate_symbols": feed.get("candidate_symbols", []),
                    "latest_cache": str(latest_path.relative_to(ROOT)),
                }
            ],
        }
    )
    write_json(TRADE_LOG_PATH, log)
    return report_path, latest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Track public manager/fund holdings and produce a backtest feed.")
    parser.add_argument("command", nargs="?", default="update", choices=["update"], help="Tracker command.")
    parser.add_argument("--source", action="append", default=[], help="Limit update to one source id. Can be repeated.")
    parser.add_argument("--timeout", type=int, default=None, help="Request timeout in seconds.")
    parser.add_argument("--max-backtest-symbols", type=int, default=None, help="Maximum symbols exported into backtest feed.")
    parser.add_argument("--allow-stale", action="store_true", help="Reuse latest cache if the fresh pull returns no holdings.")
    args = parser.parse_args(argv)
    result = build_tracker_result(args)
    report, latest = write_outputs(result)
    print(json.dumps({"status": "completed", "report": str(report), "latest": str(latest)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
