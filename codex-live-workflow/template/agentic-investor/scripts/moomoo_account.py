from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from agentic_investor_common import ROOT, enable_vendor_python, now_iso


def _prepare_moomoo_imports():
    enable_vendor_python()
    os.environ.setdefault("MOOMOO_LOG_DIR", str(ROOT / ".moomoo-logs"))
    from moomoo import OpenSecTradeContext, RET_OK, SecurityFirm, TrdEnv, TrdMarket

    return OpenSecTradeContext, RET_OK, SecurityFirm, TrdEnv, TrdMarket


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row.get(key, default)
    except AttributeError:
        return getattr(row, key, default)


def _frame_rows(frame: Any) -> list[Any]:
    if frame is None:
        return []
    try:
        length = len(frame)
    except Exception:
        return []
    rows: list[Any] = []
    for i in range(length):
        try:
            rows.append(frame.iloc[i] if hasattr(frame, "iloc") else frame[i])
        except Exception:
            continue
    return rows


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class AccountSelector:
    broker: str | None = None
    market: str | None = "US"
    env: str | None = "REAL"
    prefer_margin: bool = True


def _matches_selector(row: Any, selector: AccountSelector) -> bool:
    broker = str(_row_value(row, "broker_id", "") or _row_value(row, "broker", "") or _row_value(row, "broker_name", "")).upper()
    trd_market = str(_row_value(row, "trd_market", "")).upper()
    trd_env = str(_row_value(row, "trd_env", "")).upper()
    acc_type = str(_row_value(row, "acc_type", "") or _row_value(row, "accType", "")).upper()
    if selector.broker and selector.broker.upper() not in broker:
        return False
    if selector.market and selector.market.upper() not in trd_market:
        return False
    if selector.env and selector.env.upper() not in trd_env:
        return False
    if selector.prefer_margin:
        # tolerate unknown acc_type; only reject if explicitly CASH and we prefer margin
        if acc_type in {"CASH", "CASH_ACC", "CASH_ACCOUNT"}:
            return False
    return True


def list_accounts(host: str = "127.0.0.1", port: int = 11111) -> dict[str, Any]:
    OpenSecTradeContext, RET_OK, SecurityFirm, TrdEnv, TrdMarket = _prepare_moomoo_imports()
    ctx = OpenSecTradeContext(filter_trdmarket=TrdMarket.US, host=host, port=port, security_firm=SecurityFirm.FUTUINC)
    try:
        ret, data = ctx.get_acc_list()
        if ret != RET_OK:
            raise RuntimeError(f"get_acc_list failed: {data}")
        rows = _frame_rows(data)
        accounts = []
        for row in rows:
            accounts.append(
                {
                    "acc_id": _row_value(row, "acc_id") or _row_value(row, "accID") or _row_value(row, "accId"),
                    "trd_env": str(_row_value(row, "trd_env", "")),
                    "trd_market": str(_row_value(row, "trd_market", "")),
                    "acc_type": str(_row_value(row, "acc_type", "")),
                    "broker_id": str(_row_value(row, "broker_id", "")),
                }
            )
        return {"timestamp": now_iso(), "host": host, "port": port, "accounts": accounts}
    finally:
        ctx.close()


def _pick_account(data: Any, selector: AccountSelector) -> dict[str, Any] | None:
    rows = _frame_rows(data)
    if not rows:
        return None
    matched = [row for row in rows if _matches_selector(row, selector)]
    if not matched:
        matched = rows
    # Prefer margin if detectable.
    if selector.prefer_margin:
        margin_like = []
        for row in matched:
            acc_type = str(_row_value(row, "acc_type", "")).upper()
            if "MARGIN" in acc_type or acc_type in {"MARGIN", "MARGIN_ACC", "MARGIN_ACCOUNT"}:
                margin_like.append(row)
        if margin_like:
            matched = margin_like
    row = matched[0]
    return {
        "acc_id": _row_value(row, "acc_id") or _row_value(row, "accID") or _row_value(row, "accId"),
        "trd_env": str(_row_value(row, "trd_env", "")),
        "trd_market": str(_row_value(row, "trd_market", "")),
        "acc_type": str(_row_value(row, "acc_type", "")),
        "broker_id": str(_row_value(row, "broker_id", "")),
    }


def fetch_real_account_snapshot(
    host: str = "127.0.0.1",
    port: int = 11111,
    selector: AccountSelector | None = None,
) -> dict[str, Any]:
    OpenSecTradeContext, RET_OK, SecurityFirm, TrdEnv, TrdMarket = _prepare_moomoo_imports()
    selector = selector or AccountSelector(broker="FUTUINC", market="US", env="REAL", prefer_margin=True)
    ctx = OpenSecTradeContext(filter_trdmarket=TrdMarket.US, host=host, port=port, security_firm=SecurityFirm.FUTUINC)
    try:
        ret, acc_list = ctx.get_acc_list()
        if ret != RET_OK:
            raise RuntimeError(f"get_acc_list failed: {acc_list}")
        picked = _pick_account(acc_list, selector)
        if not picked or not picked.get("acc_id"):
            return {
                "timestamp": now_iso(),
                "status": "FAIL",
                "message": "No matching account found in get_acc_list.",
                "selector": selector.__dict__,
                "accounts": _frame_rows(acc_list),
            }

        acc_id = int(picked["acc_id"])
        assets: dict[str, Any] = {}
        positions: list[dict[str, Any]] = []
        orders: list[dict[str, Any]] = []
        risk: dict[str, Any] = {}
        errors: dict[str, str] = {}

        try:
            ret, data = ctx.accinfo_query(trd_env=TrdEnv.REAL, acc_id=acc_id)
            if ret != RET_OK:
                raise RuntimeError(str(data))
            row = _frame_rows(data)[0] if _frame_rows(data) else None
            if row is not None:
                assets = {
                    "total_assets": _float(_row_value(row, "total_assets")),
                    "net_assets": _float(_row_value(row, "net_assets")),
                    "cash": _float(_row_value(row, "cash")),
                    "power": _float(_row_value(row, "power")),
                    "max_power_short": _float(_row_value(row, "max_power_short")),
                    "margin_call_status": str(_row_value(row, "margin_call_status", "")),
                    "risk_level": str(_row_value(row, "risk_level", "")),
                    "maintenance_margin": _float(_row_value(row, "maintenance_margin")),
                }
        except Exception as exc:
            errors["accinfo_query"] = str(exc)

        try:
            ret, data = ctx.position_list_query(trd_env=TrdEnv.REAL, acc_id=acc_id)
            if ret != RET_OK:
                raise RuntimeError(str(data))
            for row in _frame_rows(data):
                positions.append(
                    {
                        "code": str(_row_value(row, "code", "")),
                        "symbol": str(_row_value(row, "code", "")).split(".")[-1],
                        "qty": _float(_row_value(row, "qty")),
                        "can_sell_qty": _float(_row_value(row, "can_sell_qty")),
                        "cost_price": _float(_row_value(row, "cost_price")),
                        "cost": _float(_row_value(row, "cost")),
                        "market_val": _float(_row_value(row, "market_val")),
                        "pl_val": _float(_row_value(row, "pl_val")),
                        "pl_ratio": _float(_row_value(row, "pl_ratio")),
                        "today_pl_val": _float(_row_value(row, "today_pl_val")),
                        "today_pl_ratio": _float(_row_value(row, "today_pl_ratio")),
                    }
                )
        except Exception as exc:
            errors["position_list_query"] = str(exc)

        try:
            # Keep conservative defaults: query open orders only; do not touch history/trade list.
            ret, data = ctx.order_list_query(trd_env=TrdEnv.REAL, acc_id=acc_id)
            if ret != RET_OK:
                raise RuntimeError(str(data))
            for row in _frame_rows(data):
                orders.append(
                    {
                        "code": str(_row_value(row, "code", "")),
                        "symbol": str(_row_value(row, "code", "")).split(".")[-1],
                        "order_id": _row_value(row, "order_id"),
                        "status": str(_row_value(row, "order_status", "")),
                        "side": str(_row_value(row, "trd_side", "")),
                        "order_type": str(_row_value(row, "order_type", "")),
                        "qty": _float(_row_value(row, "qty")),
                        "price": _float(_row_value(row, "price")),
                        "create_time": str(_row_value(row, "create_time", "")),
                        "updated_time": str(_row_value(row, "updated_time", "")),
                    }
                )
        except Exception as exc:
            errors["order_list_query"] = str(exc)

        # Attempt lightweight risk fields if they exist in accinfo; otherwise keep empty.
        risk = {
            "margin_call_status": assets.get("margin_call_status"),
            "risk_level": assets.get("risk_level"),
        }

        return {
            "timestamp": now_iso(),
            "status": "PASS" if not errors else "WARN",
            "message": "Read-only REAL account snapshot fetched. No unlock_trade, no orders.",
            "source": {"broker": "moomoo OpenD", "host": host, "port": port},
            "account": picked,
            "assets": assets,
            "risk": risk,
            "positions": positions,
            "open_orders": orders,
            "errors": errors,
            "assumptions": {
                "execution": "read-only account/positions/orders; no unlock_trade; no order placement/cancel/modify",
            },
        }
    finally:
        ctx.close()


def main() -> int:
    import json

    snapshot = fetch_real_account_snapshot()
    def jsonify(value: Any) -> Any:
        if hasattr(value, "item") and callable(value.item):
            try:
                return jsonify(value.item())
            except Exception:
                return str(value)
        if isinstance(value, dict):
            return {str(k): jsonify(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [jsonify(v) for v in value]
        if isinstance(value, float):
            return value if value == value and value not in (float("inf"), float("-inf")) else None
        return value

    print(json.dumps(jsonify(snapshot), ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if snapshot.get("status") in {"PASS", "WARN"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
