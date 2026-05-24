from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from typing import Any

from agentic_investor_common import ROOT, RULE_PATH, STATE_PATH, read_json
from macro_regime import classify_macro, load_macro, overlay_for
from watchlist_manager import active_items, excluded_symbols, load_watchlist, normalize_symbol


@dataclass(frozen=True)
class GuardResult:
    name: str
    status: str
    message: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "details": self.details or {},
        }


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _active_watchlist_by_symbol(watchlist: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["normalized_symbol"]: item for item in active_items(watchlist)}


def _intent_notional(intent: dict[str, Any]) -> float | None:
    notional = _float(intent.get("notional"))
    if notional and notional > 0:
        return notional
    quantity = _float(intent.get("quantity"))
    limit_price = _float(intent.get("limit_price"))
    if quantity and limit_price and quantity > 0 and limit_price > 0:
        return quantity * limit_price
    return None


def _guard_schema(intent: dict[str, Any]) -> GuardResult:
    missing = []
    for key in ("symbol", "side", "rationale"):
        if not str(intent.get(key) or "").strip():
            missing.append(key)
    if missing:
        return GuardResult("schema", "REJECT", f"Missing required field(s): {', '.join(missing)}")

    side = str(intent.get("side")).upper()
    if side not in {"BUY", "SELL"}:
        return GuardResult("schema", "REJECT", "Only BUY and SELL advisory intents are supported.", {"side": side})

    if not _intent_notional(intent) and _float(intent.get("target_weight")) is None:
        return GuardResult(
            "schema",
            "WARN",
            "No notional, quantity/limit price, or target weight supplied; sizing needs manual review.",
        )
    return GuardResult("schema", "PASS", "Intent has the required advisory fields.")


def _guard_safety_mode(state: dict[str, Any], intent: dict[str, Any]) -> GuardResult:
    if state.get("paused"):
        return GuardResult("safety_mode", "REJECT", "Strategy is paused.")
    if intent.get("broker_execution_requested"):
        return GuardResult("safety_mode", "REJECT", "Broker execution is disabled for order intents.")
    if state.get("mode") == "REAL":
        return GuardResult(
            "safety_mode",
            "WARN",
            "REAL mode requires a separate approval packet; this intent remains advisory-only.",
        )
    return GuardResult("safety_mode", "PASS", "Advisory-only workflow is allowed by current state.")


def _baseline_symbols(rules: dict[str, Any]) -> set[str]:
    symbols = {str(symbol).upper() for symbol in rules.get("universe", [])}
    portfolio = rules.get("portfolio_rules", {})
    for tier in ("tier_core", "tier_satellite"):
        symbols.update(str(symbol).upper() for symbol in portfolio.get(tier, {}).get("stocks", []))
    for strategy in rules.get("option_rules", {}).get("strategies", []):
        symbols.update(str(symbol).upper() for symbol in strategy.get("underlyings", []))
    symbols.update(str(symbol).upper() for symbol in rules.get("order_intent_rules", {}).get("baseline_symbols", []))
    symbols.update({"QQQ", "SPY", "SMH", "SOXX"})
    return {symbol for symbol in symbols if symbol}


def _guard_symbol(watchlist: dict[str, Any], rules: dict[str, Any], intent: dict[str, Any]) -> GuardResult:
    symbol = normalize_symbol(str(intent.get("symbol", "")), watchlist.get("symbol_aliases", {}))
    if symbol in excluded_symbols(watchlist):
        return GuardResult("symbol", "REJECT", "Symbol is explicitly excluded by the user.", {"symbol": symbol})
    if symbol in _baseline_symbols(rules):
        return GuardResult("symbol", "PASS", "Symbol is part of the configured strategy universe or baseline ETF set.", {"symbol": symbol})
    if intent.get("side", "").upper() == "BUY" and symbol not in _active_watchlist_by_symbol(watchlist):
        return GuardResult("symbol", "WARN", "Symbol is not in active watchlist; require manual thesis review.", {"symbol": symbol})
    return GuardResult("symbol", "PASS", "Symbol is not excluded.", {"symbol": symbol})


def _guard_thesis(intent: dict[str, Any], watchlist: dict[str, Any]) -> GuardResult:
    symbol = normalize_symbol(str(intent.get("symbol", "")), watchlist.get("symbol_aliases", {}))
    watch_item = _active_watchlist_by_symbol(watchlist).get(symbol, {})
    rationale = str(intent.get("rationale") or "").strip()
    invalidation = str(intent.get("invalidation") or "").strip()
    entry_trigger = str(intent.get("entry_trigger") or "").strip()
    source = str(intent.get("source") or watch_item.get("source") or "").lower()
    tags = " ".join(watch_item.get("tags", [])).lower()

    if len(rationale) < 24:
        return GuardResult("thesis", "REJECT", "Rationale is too short to audit.")
    if not invalidation:
        return GuardResult("thesis", "WARN", "Missing invalidation/stop rule.")
    if not entry_trigger and intent.get("side", "").upper() == "BUY":
        return GuardResult("thesis", "WARN", "Missing entry trigger for a BUY intent.")
    if any(token in source or token in tags for token in ("reddit", "x", "social", "buzz")) and not invalidation:
        return GuardResult("thesis", "REJECT", "Social-buzz ideas need an explicit invalidation rule before staging.")
    return GuardResult("thesis", "PASS", "Thesis and invalidation are auditable.")


def _guard_macro(intent: dict[str, Any]) -> GuardResult:
    macro = load_macro()
    regime = classify_macro(macro)
    overlay = overlay_for(macro, regime)
    side = str(intent.get("side") or "").upper()
    instrument_type = str(intent.get("instrument_type") or "EQUITY").upper()
    if side == "BUY" and regime == "RISK_OFF":
        return GuardResult("macro", "REJECT", "Macro regime is RISK_OFF; new risk is blocked.", {"regime": regime, "overlay": overlay})
    if side == "BUY" and instrument_type in {"OPTION", "OPTIONS"} and not overlay.get("new_options_allowed"):
        return GuardResult("macro", "REJECT", "Macro overlay blocks new option risk.", {"regime": regime, "overlay": overlay})
    if side == "BUY" and regime in {"HAWKISH", "UNKNOWN"}:
        return GuardResult("macro", "WARN", "Macro overlay requires conservative sizing/manual review.", {"regime": regime, "overlay": overlay})
    return GuardResult("macro", "PASS", "Macro overlay does not block this advisory intent.", {"regime": regime, "overlay": overlay})


def _guard_earnings_event(intent: dict[str, Any]) -> GuardResult:
    instrument_type = str(intent.get("instrument_type") or "EQUITY").upper()
    if instrument_type not in {"OPTION", "OPTIONS"}:
        return GuardResult("earnings_event", "PASS", "Earnings-event option gate does not apply to non-option intent.")

    latest_path = ROOT / "data" / "events" / "earnings_latest.json"
    if not latest_path.exists():
        return GuardResult("earnings_event", "WARN", "No earnings event-risk cache found; run earnings_event_risk first.")

    latest = read_json(latest_path, {})
    symbol = str(intent.get("normalized_symbol") or intent.get("symbol") or "").upper()
    blocked = set(latest.get("blocked_option_symbols", []))
    review = set(latest.get("review_option_symbols", []))
    event = next((row for row in latest.get("events", []) if row.get("symbol") == symbol), None)
    if symbol in blocked:
        return GuardResult(
            "earnings_event",
            "REJECT",
            "Upcoming earnings is inside the option blackout window.",
            {"symbol": symbol, "event": event},
        )
    if symbol in review:
        return GuardResult(
            "earnings_event",
            "WARN",
            "Upcoming earnings requires manual option-risk review.",
            {"symbol": symbol, "event": event},
        )
    return GuardResult("earnings_event", "PASS", "No near-term earnings event blocks this option intent.", {"symbol": symbol})


def _guard_position_size(state: dict[str, Any], rules: dict[str, Any], intent: dict[str, Any]) -> GuardResult:
    start_capital = _float(state.get("start_capital"), 0.0) or 0.0
    notional = _intent_notional(intent)
    target_weight = _float(intent.get("target_weight"))
    max_single = _float(rules.get("risk_management", {}).get("position_limit", {}).get("max_single_stock"), 0.20) or 0.20

    if target_weight is not None and target_weight > max_single:
        return GuardResult("position_size", "REJECT", "Target weight exceeds max single-stock limit.", {"target_weight": target_weight, "max_single_stock": max_single})
    if notional is not None and start_capital > 0 and notional / start_capital > max_single:
        return GuardResult(
            "position_size",
            "REJECT",
            "Notional exceeds max single-stock limit.",
            {"notional": notional, "capital": start_capital, "max_single_stock": max_single},
        )
    if notional is None and target_weight is None:
        return GuardResult("position_size", "WARN", "Position size is not fully specified.")
    return GuardResult("position_size", "PASS", "Sizing is inside configured single-name limits.")


def _snapshot_market_row(symbol: str) -> dict[str, Any] | None:
    latest_path = ROOT / "data" / "market" / "latest.json"
    if not latest_path.exists():
        return None
    try:
        latest = read_json(latest_path, {})
    except Exception:
        return None
    normalized = symbol.upper()
    for section in ("watchSymbols", "indices", "sectorEtfs", "macroProxies"):
        for row in latest.get(section, []) or []:
            if str(row.get("symbol", "")).upper() == normalized:
                return {**row, "section": section, "snapshotTimestamp": latest.get("timestamp")}
    return None


def _market_data_snapshot_fallback(symbol: str, side: str, original_error: str) -> GuardResult:
    row = _snapshot_market_row(symbol)
    status = "REJECT" if side == "BUY" else "WARN"
    if not row:
        return GuardResult(
            "market_data",
            status,
            "Market data check failed and no latest snapshot fallback was found.",
            {"symbol": symbol, "error": original_error},
        )

    last = _float(row.get("last"), 0.0) or 0.0
    if side == "BUY" and last <= 0:
        return GuardResult(
            "market_data",
            "REJECT",
            "Latest market snapshot did not contain a usable price.",
            {"symbol": symbol, "last": last, "error": original_error},
        )
    return GuardResult(
        "market_data",
        "WARN",
        "Live moomoo data was unavailable; using latest market snapshot fallback.",
        {
            "symbol": symbol,
            "last_price": last,
            "day_change_pct": row.get("dayChangePct"),
            "momentum_30d_pct": row.get("momentum30dPct"),
            "above_ma50": row.get("aboveMa50"),
            "source": row.get("source"),
            "as_of": row.get("asOf"),
            "snapshot_timestamp": row.get("snapshotTimestamp"),
            "fallback": "data/market/latest.json",
            "original_error": original_error,
        },
    )


def _guard_market_data(intent: dict[str, Any], refresh_market_data: bool) -> GuardResult:
    if not refresh_market_data:
        return GuardResult("market_data", "WARN", "Market data refresh was skipped by caller.")

    symbol = str(intent.get("normalized_symbol") or intent.get("symbol") or "")
    side = str(intent.get("side", "")).upper()
    moomoo_output = io.StringIO()
    try:
        with redirect_stdout(moomoo_output), redirect_stderr(moomoo_output):
            from moomoo_data import fetch_research_snapshot

            research = fetch_research_snapshot([symbol])
        records = research.get("records", [])
        if not records:
            code = f"US.{symbol}"
            error = research.get("errors", {}).get(code, "No market record returned.")
            status = "REJECT" if side == "BUY" else "WARN"
            return GuardResult("market_data", status, "No usable moomoo market data.", {"symbol": symbol, "error": error})
        row = records[0]
        last = _float(row.get("last_price"), 0.0) or 0.0
        avg_volume = _float(row.get("avg_volume_20d"), 0.0) or 0.0
        dollar_volume = last * avg_volume
        if side == "BUY" and dollar_volume and dollar_volume < 20_000_000:
            return GuardResult(
                "market_data",
                "WARN",
                "Dollar volume is below the default liquidity comfort line.",
                {"symbol": symbol, "last_price": last, "avg_volume_20d": avg_volume, "avg_dollar_volume_20d": dollar_volume},
            )
        return GuardResult(
            "market_data",
            "PASS",
            "moomoo market data is available.",
            {
                "symbol": symbol,
                "last_price": last,
                "momentum_30d_pct": row.get("momentum_30d_pct"),
                "above_ma50": row.get("above_ma50"),
                "avg_volume_20d": avg_volume,
                "avg_dollar_volume_20d": dollar_volume,
            },
        )
    except BaseException as exc:
        error_text = str(exc)
        captured = moomoo_output.getvalue().strip()
        if captured:
            error_text = f"{captured} ({error_text})" if error_text else captured
        return _market_data_snapshot_fallback(symbol, side, error_text)


def run_guard_pipeline(intent: dict[str, Any], refresh_market_data: bool = True) -> dict[str, Any]:
    state = read_json(STATE_PATH)
    rules = read_json(RULE_PATH)
    watchlist = load_watchlist()
    normalized = normalize_symbol(str(intent.get("symbol", "")), watchlist.get("symbol_aliases", {}))
    normalized_intent = {**intent, "symbol": normalized, "normalized_symbol": normalized}

    guards = [
        _guard_schema(normalized_intent),
        _guard_safety_mode(state, normalized_intent),
        _guard_symbol(watchlist, rules, normalized_intent),
        _guard_thesis(normalized_intent, watchlist),
        _guard_macro(normalized_intent),
        _guard_earnings_event(normalized_intent),
        _guard_position_size(state, rules, normalized_intent),
        _guard_market_data(normalized_intent, refresh_market_data),
    ]
    statuses = [guard.status for guard in guards]
    if "REJECT" in statuses:
        status = "REJECTED"
    elif "WARN" in statuses:
        status = "REQUIRES_REVIEW"
    else:
        status = "PASSED"
    return {
        "status": status,
        "intent": normalized_intent,
        "guards": [guard.to_dict() for guard in guards],
        "policy": {
            "advisory_only": True,
            "broker_execution_enabled": False,
            "root": str(ROOT),
        },
    }
