from __future__ import annotations

import json
import math
from pathlib import Path
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


TRADING_DIR = ROOT / "data" / "trading"
STAGED_ORDERS_PATH = TRADING_DIR / "staged-orders.json"
PORTFOLIO_PATH = TRADING_DIR / "paper-portfolio.json"
FILLS_PATH = TRADING_DIR / "paper-fills.jsonl"
NAV_PATH = TRADING_DIR / "paper-nav.jsonl"
MARKET_SNAPSHOT_PATH = ROOT / "data" / "market" / "latest.json"

APPROVED_STATUSES = {"COMMITTED_REQUIRES_REVIEW", "COMMITTED", "COMMITTED_READY", "COMMITTED_PREAPPROVED"}
FILLED_STATUS = "PAPER_FILLED"


def _money(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _round(value: Any, digits: int = 2) -> float:
    return round(_money(value), digits)


def _load_market_prices() -> dict[str, dict[str, Any]]:
    market = read_json(MARKET_SNAPSHOT_PATH, {})
    prices: dict[str, dict[str, Any]] = {}
    for section in ("indices", "macroProxies", "sectorEtfs", "watchSymbols", "trueMacroSeries"):
        for row in market.get(section, []):
            symbol = row.get("symbol")
            last = row.get("last")
            if symbol and last is not None:
                prices[str(symbol).upper()] = {
                    "price": _money(last),
                    "source": f"market_snapshot.{section}",
                    "timestamp": market.get("timestamp"),
                    "dataQuality": row.get("dataQuality"),
                    "dayChangePct": row.get("dayChangePct"),
                    "momentum30dPct": row.get("momentum30dPct"),
                }
    return prices


def _guard_price(order: dict[str, Any]) -> dict[str, Any] | None:
    for guard in order.get("guard_result", {}).get("guards", []):
        if guard.get("name") == "market_data":
            details = guard.get("details", {})
            last = details.get("last_price")
            if last is not None:
                return {
                    "price": _money(last),
                    "source": "order_guard.market_data",
                    "timestamp": order.get("updated_at") or order.get("created_at"),
                    "dataQuality": guard.get("status"),
                    "dayChangePct": details.get("day_change_pct"),
                    "momentum30dPct": details.get("momentum_30d_pct"),
                }
    return None


def _intent_price(order: dict[str, Any]) -> dict[str, Any] | None:
    intent = order.get("intent", {})
    limit_price = intent.get("limit_price")
    if limit_price is not None:
        return {
            "price": _money(limit_price),
            "source": "intent.limit_price",
            "timestamp": order.get("updated_at") or order.get("created_at"),
            "dataQuality": "FALLBACK",
        }
    return None


def _price_for_order(order: dict[str, Any], market_prices: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    intent = order.get("intent", {})
    symbol = str(intent.get("normalized_symbol") or intent.get("symbol") or "").upper()
    if symbol in market_prices:
        return market_prices[symbol]
    return _guard_price(order) or _intent_price(order)


def _empty_portfolio(start_capital: float) -> dict[str, Any]:
    return {
        "version": "1.0",
        "last_updated": now_iso(),
        "source": "local paper fill engine",
        "policy": {
            "advisory_only": True,
            "broker_execution_enabled": False,
            "account_read_enabled": False,
            "live_order_placement": False,
        },
        "start_capital": round(start_capital, 2),
        "cash": round(start_capital, 2),
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "nav": round(start_capital, 2),
        "positions": [],
        "filled_intent_ids": [],
        "fill_count": 0,
        "nav_history": [],
    }


def _load_portfolio(start_capital: float) -> dict[str, Any]:
    portfolio = read_json(PORTFOLIO_PATH, _empty_portfolio(start_capital))
    portfolio.setdefault("positions", [])
    portfolio.setdefault("filled_intent_ids", [])
    portfolio.setdefault("cash", start_capital)
    portfolio.setdefault("realized_pnl", 0.0)
    portfolio.setdefault("nav", start_capital)
    portfolio.setdefault("policy", {})
    portfolio["policy"].update(
        {
            "advisory_only": True,
            "broker_execution_enabled": False,
            "account_read_enabled": False,
            "live_order_placement": False,
        }
    )
    return portfolio


def _position_key(row: dict[str, Any]) -> str:
    if str(row.get("instrumentType") or "").upper() in {"OPTION", "OPTIONS"}:
        return str(row.get("contractSymbol") or row.get("symbol") or "").upper()
    return str(row.get("symbol") or "").upper()


def _position_map(portfolio: dict[str, Any]) -> dict[str, dict[str, Any]]:
    positions: dict[str, dict[str, Any]] = {}
    for row in portfolio.get("positions", []):
        key = _position_key(row)
        if key:
            positions[key] = row
    return positions


def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _target_notional(intent: dict[str, Any], nav: float, existing_value: float) -> float:
    explicit_notional = intent.get("notional")
    if explicit_notional is not None:
        return max(0.0, _money(explicit_notional))
    target_weight = intent.get("target_weight")
    if target_weight is not None:
        return max(0.0, nav * _money(target_weight) - existing_value)
    return 0.0


def _mark_positions(
    portfolio: dict[str, Any],
    market_prices: dict[str, dict[str, Any]],
    fallback_prices: dict[str, float],
) -> None:
    total_market_value = 0.0
    total_unrealized = 0.0
    for position in portfolio.get("positions", []):
        symbol = str(position.get("symbol") or "").upper()
        instrument_type = str(position.get("instrumentType") or "EQUITY").upper()
        multiplier = _money(position.get("multiplier")) or 1.0
        quantity = _money(position.get("quantity"))
        avg_cost = _money(position.get("avgCost"))
        price_info = market_prices.get(symbol) if instrument_type == "EQUITY" else None
        key = _position_key(position)
        last_price = _money(price_info.get("price")) if price_info else fallback_prices.get(key, _money(position.get("lastPrice")) or avg_cost)
        cost_basis = quantity * avg_cost * multiplier
        market_value = quantity * last_price * multiplier
        pnl = market_value - cost_basis
        position.update(
            {
                "symbol": symbol,
                "instrumentType": instrument_type,
                "quantity": int(quantity) if quantity.is_integer() else quantity,
                "avgCost": _round(avg_cost, 4),
                "lastPrice": _round(last_price, 4),
                "marketValue": _round(market_value),
                "costBasis": _round(cost_basis),
                "pnl": _round(pnl),
                "pnlPct": _round((pnl / cost_basis * 100) if cost_basis else 0),
                "priceSource": price_info.get("source") if price_info else "fill_price_fallback",
                "lastMarketTimestamp": price_info.get("timestamp") if price_info else None,
            }
        )
        total_market_value += market_value
        total_unrealized += pnl

    nav = _money(portfolio.get("cash")) + total_market_value
    for position in portfolio.get("positions", []):
        position["weightPct"] = _round((_money(position.get("marketValue")) / nav * 100) if nav else 0)
    portfolio["unrealized_pnl"] = _round(total_unrealized)
    portfolio["nav"] = _round(nav)


def build_paper_fill_result(state: dict[str, Any]) -> dict[str, Any]:
    timestamp = now_iso()
    start_capital = _money(state.get("start_capital")) or 135000.0
    staged = read_json(STAGED_ORDERS_PATH, {"orders": []})
    portfolio = _load_portfolio(start_capital)
    market_prices = _load_market_prices()
    positions = _position_map(portfolio)
    filled_ids = {str(item) for item in portfolio.get("filled_intent_ids", [])}
    new_fills: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    fallback_prices: dict[str, float] = {}

    for order in staged.get("orders", []):
        intent_id = str(order.get("intent_id") or "")
        intent = order.get("intent", {})
        symbol = str(intent.get("normalized_symbol") or intent.get("symbol") or "").upper()
        status = str(order.get("status") or "")

        if not intent_id or intent_id in filled_ids or status == FILLED_STATUS:
            continue
        if status not in APPROVED_STATUSES:
            continue
        side = str(intent.get("side") or "").upper()
        instrument_type = str(intent.get("instrument_type") or "EQUITY").upper()
        if side not in {"BUY", "SELL"} or instrument_type not in {"EQUITY", "OPTION", "OPTIONS"}:
            skipped.append(
                {
                    "intent_id": intent_id,
                    "symbol": symbol,
                    "reason": "Only BUY/SELL EQUITY and long OPTION paper intents are supported by the current paper fill engine.",
                }
            )
            continue

        option_contract = intent.get("option_contract") or {}
        contract_symbol = str(
            intent.get("contract_symbol")
            or option_contract.get("contract_symbol")
            or option_contract.get("symbol")
            or ""
        ).upper()
        position_key = contract_symbol if instrument_type in {"OPTION", "OPTIONS"} else symbol
        multiplier = 100.0 if instrument_type in {"OPTION", "OPTIONS"} else 1.0
        price_info = _price_for_order(order, market_prices) if instrument_type == "EQUITY" else _intent_price(order)
        price = _money(price_info.get("price")) if price_info else 0.0
        if price <= 0:
            skipped.append({"intent_id": intent_id, "symbol": symbol, "reason": "No valid fill price. Options require explicit limit_price/premium."})
            continue

        if instrument_type in {"OPTION", "OPTIONS"} and not position_key:
            skipped.append({"intent_id": intent_id, "symbol": symbol, "reason": "Option paper fill requires contract_symbol or option_contract.symbol."})
            continue

        if side == "SELL" and position_key not in positions:
            skipped.append({"intent_id": intent_id, "symbol": symbol, "reason": "No paper position to sell."})
            continue
        position = positions.setdefault(
            position_key,
            {
                "symbol": position_key if instrument_type in {"OPTION", "OPTIONS"} else symbol,
                "underlying": symbol if instrument_type in {"OPTION", "OPTIONS"} else None,
                "instrumentType": instrument_type,
                "contractSymbol": contract_symbol if instrument_type in {"OPTION", "OPTIONS"} else None,
                "optionContract": option_contract if instrument_type in {"OPTION", "OPTIONS"} else None,
                "multiplier": multiplier,
                "quantity": 0,
                "avgCost": 0.0,
                "strategy": intent.get("strategy"),
                "sourceIntentIds": [],
            },
        )
        existing_value = _money(position.get("quantity")) * price * multiplier
        nav_for_sizing = _money(portfolio.get("nav")) or start_capital

        explicit_quantity = intent.get("quantity")
        if explicit_quantity is not None:
            quantity = math.floor(_money(explicit_quantity))
        elif side == "SELL" and intent.get("target_weight") is not None:
            target_value = nav_for_sizing * _money(intent.get("target_weight"))
            quantity = math.floor(max(0.0, existing_value - target_value) / price)
        elif side == "SELL":
            quantity = math.floor(_money(position.get("quantity")))
        else:
            target_notional = _target_notional(intent, nav_for_sizing, existing_value)
            quantity = math.floor(min(target_notional, _money(portfolio.get("cash"))) / (price * multiplier))

        if quantity <= 0:
            skipped.append(
                {
                    "intent_id": intent_id,
                    "symbol": symbol,
                    "reason": "Sizing produced zero whole shares after cash/target-weight checks.",
                    "price": round(price, 4),
                }
            )
            continue

        if side == "SELL":
            quantity = min(quantity, math.floor(_money(position.get("quantity"))))
        gross_notional = quantity * price * multiplier
        if side == "BUY" and gross_notional > _money(portfolio.get("cash")) + 1e-6:
            quantity = math.floor(_money(portfolio.get("cash")) / (price * multiplier))
            gross_notional = quantity * price * multiplier
        if quantity <= 0:
            skipped.append({"intent_id": intent_id, "symbol": symbol, "reason": "Insufficient paper cash."})
            continue

        old_qty = _money(position.get("quantity"))
        avg_cost = _money(position.get("avgCost"))
        old_cost_basis = old_qty * avg_cost * multiplier
        realized_pnl = 0.0
        if side == "BUY":
            new_qty = old_qty + quantity
            new_cost_basis = old_cost_basis + gross_notional
            position.update(
                {
                    "symbol": position_key if instrument_type in {"OPTION", "OPTIONS"} else symbol,
                    "underlying": symbol if instrument_type in {"OPTION", "OPTIONS"} else None,
                    "instrumentType": instrument_type,
                    "contractSymbol": contract_symbol if instrument_type in {"OPTION", "OPTIONS"} else None,
                    "optionContract": option_contract if instrument_type in {"OPTION", "OPTIONS"} else None,
                    "multiplier": multiplier,
                    "quantity": int(new_qty) if new_qty.is_integer() else new_qty,
                    "avgCost": _round(new_cost_basis / (new_qty * multiplier), 4),
                    "costBasis": _round(new_cost_basis),
                    "strategy": intent.get("strategy"),
                    "updatedAt": timestamp,
                }
            )
            portfolio["cash"] = _round(_money(portfolio.get("cash")) - gross_notional)
        else:
            new_qty = old_qty - quantity
            sold_cost_basis = quantity * avg_cost * multiplier
            realized_pnl = gross_notional - sold_cost_basis
            new_cost_basis = max(0.0, old_cost_basis - sold_cost_basis)
            position.update(
                {
                    "symbol": position_key if instrument_type in {"OPTION", "OPTIONS"} else symbol,
                    "underlying": symbol if instrument_type in {"OPTION", "OPTIONS"} else None,
                    "instrumentType": instrument_type,
                    "contractSymbol": contract_symbol if instrument_type in {"OPTION", "OPTIONS"} else None,
                    "optionContract": option_contract if instrument_type in {"OPTION", "OPTIONS"} else None,
                    "multiplier": multiplier,
                    "quantity": int(new_qty) if float(new_qty).is_integer() else new_qty,
                    "avgCost": _round(avg_cost, 4),
                    "costBasis": _round(new_cost_basis),
                    "strategy": intent.get("strategy"),
                    "updatedAt": timestamp,
                }
            )
            portfolio["cash"] = _round(_money(portfolio.get("cash")) + gross_notional)
            portfolio["realized_pnl"] = _round(_money(portfolio.get("realized_pnl")) + realized_pnl)
        source_ids = set(position.get("sourceIntentIds", []))
        source_ids.add(intent_id)
        position["sourceIntentIds"] = sorted(source_ids)

        filled_ids.add(intent_id)
        fallback_prices[position_key] = price

        fill = {
            "fill_id": f"paper-{intent_id}",
            "timestamp": timestamp,
            "intent_id": intent_id,
            "commit_hash": order.get("commit_hash"),
            "symbol": position_key if instrument_type in {"OPTION", "OPTIONS"} else symbol,
            "underlying": symbol if instrument_type in {"OPTION", "OPTIONS"} else None,
            "side": side,
            "instrument_type": instrument_type,
            "contract_symbol": contract_symbol if instrument_type in {"OPTION", "OPTIONS"} else None,
            "multiplier": multiplier,
            "quantity": quantity,
            "fill_price": _round(price, 4),
            "gross_notional": _round(gross_notional),
            "commission": 0.0,
            "slippage_bps": 0.0,
            "price_source": price_info.get("source") if price_info else "unknown",
            "market_timestamp": price_info.get("timestamp") if price_info else None,
            "execution_policy": "PAPER_ONLY_NO_BROKER_ORDER_NO_ACCOUNT_READ",
            "target_weight": intent.get("target_weight"),
            "rationale": intent.get("rationale"),
            "realized_pnl": _round(realized_pnl),
        }
        new_fills.append(fill)
        order["status"] = FILLED_STATUS
        order["updated_at"] = timestamp
        order["paper_fill"] = fill

    portfolio["positions"] = sorted(
        [row for row in positions.values() if _money(row.get("quantity")) > 0],
        key=lambda row: row.get("symbol", ""),
    )
    portfolio["filled_intent_ids"] = sorted(filled_ids)
    portfolio["fill_count"] = len(portfolio["filled_intent_ids"])
    portfolio["last_updated"] = timestamp
    _mark_positions(portfolio, market_prices, fallback_prices)
    portfolio.setdefault("nav_history", [])
    nav_point = {
        "timestamp": timestamp,
        "nav": portfolio.get("nav"),
        "cash": portfolio.get("cash"),
        "market_value": _round(sum(_money(row.get("marketValue")) for row in portfolio.get("positions", []))),
        "unrealized_pnl": portfolio.get("unrealized_pnl"),
    }
    portfolio["nav_history"].append(nav_point)

    write_json(PORTFOLIO_PATH, portfolio)
    write_json(STAGED_ORDERS_PATH, staged)
    _append_jsonl(FILLS_PATH, new_fills)
    _append_jsonl(NAV_PATH, [nav_point])

    status = "FILLED" if new_fills else "NO_NEW_FILLS"
    result = {
        "task": "paper_fill_engine",
        "timestamp": timestamp,
        "status": status,
        "new_fill_count": len(new_fills),
        "new_fills": new_fills,
        "skipped": skipped,
        "portfolio": {
            "path": str(PORTFOLIO_PATH.relative_to(ROOT)),
            "nav": portfolio.get("nav"),
            "cash": portfolio.get("cash"),
            "unrealized_pnl": portfolio.get("unrealized_pnl"),
            "positions": portfolio.get("positions", []),
        },
        "policy": portfolio.get("policy"),
    }
    return result


def write_outputs(result: dict[str, Any]) -> tuple[Path, Path]:
    report_path = REPORTS_DIR / f"{today_stamp()}_paper_fill_engine.md"
    lines = [
        "# Paper Fill Engine",
        "",
        f"- Timestamp: {result.get('timestamp')}",
        f"- Status: {result.get('status')}",
        f"- New fills: {result.get('new_fill_count')}",
        "- Safety: 本任务只写本地模拟成交，不提交券商订单，不读取或修改真实账户。",
        "",
        "## Fills",
        "",
    ]
    fills = result.get("new_fills", [])
    if fills:
        lines += [
            "| Symbol | Qty | Fill | Notional | Source | Intent |",
            "|---|---:|---:|---:|---|---|",
        ]
        for fill in fills:
            lines.append(
                f"| {fill.get('symbol')} | {fill.get('quantity')} | {fill.get('fill_price')} | "
                f"{fill.get('gross_notional')} | {fill.get('price_source')} | {fill.get('intent_id')} |"
            )
    else:
        lines.append("- No new approved intent needed a paper fill.")

    portfolio = result.get("portfolio", {})
    lines += [
        "",
        "## Portfolio",
        "",
        f"- NAV: {portfolio.get('nav')}",
        f"- Cash: {portfolio.get('cash')}",
        f"- Unrealized P&L: {portfolio.get('unrealized_pnl')}",
        "",
        "| Symbol | Qty | Avg Cost | Last | Market Value | P&L | Weight |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for position in portfolio.get("positions", []):
        lines.append(
            f"| {position.get('symbol')} | {position.get('quantity')} | {position.get('avgCost')} | "
            f"{position.get('lastPrice')} | {position.get('marketValue')} | {position.get('pnl')} | "
            f"{position.get('weightPct')}% |"
        )

    if result.get("skipped"):
        lines += ["", "## Skipped", ""]
        for row in result.get("skipped", []):
            lines.append(f"- {row.get('symbol')} / {row.get('intent_id')}: {row.get('reason')}")

    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report_path, PORTFOLIO_PATH


def run_paper_fill_engine(state: dict[str, Any]) -> dict[str, Any]:
    result = build_paper_fill_result(state)
    report_path, portfolio_path = write_outputs(result)
    append_trade_log(
        {
            "timestamp": result["timestamp"],
            "task": "paper_fill_engine",
            "status": result["status"],
            "summary": f"Processed {result['new_fill_count']} local paper fills; no broker order/account read.",
            "report": str(report_path.relative_to(ROOT)),
            "proposals": result.get("new_fills", []),
        }
    )
    result["report"] = str(report_path)
    result["portfolio_path"] = str(portfolio_path)
    return result


def main() -> int:
    state = read_json(ROOT / "state.json", {})
    result = run_paper_fill_engine(state)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
