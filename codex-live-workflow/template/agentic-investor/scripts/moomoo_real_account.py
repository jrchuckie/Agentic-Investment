from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from agentic_investor_common import ROOT, enable_vendor_python, now_iso, today_stamp, write_json, write_report


def _prepare_moomoo_imports():
    enable_vendor_python()
    os.environ.setdefault("MOOMOO_LOG_DIR", str(ROOT / ".moomoo-logs"))
    from moomoo import OpenSecTradeContext, RET_OK, SecurityFirm, TrdEnv, TrdMarket

    return OpenSecTradeContext, RET_OK, SecurityFirm, TrdEnv, TrdMarket


def _item(value: Any) -> Any:
    if value is None:
        return None
    try:
        if hasattr(value, "item"):
            return value.item()
    except Exception:
        return value
    return value


def _frame_to_records(frame: Any) -> list[dict[str, Any]]:
    if frame is None or len(frame) == 0:
        return []
    cols = list(getattr(frame, "columns", []))
    rows: list[dict[str, Any]] = []
    for i in range(len(frame)):
        row = frame.iloc[i] if hasattr(frame, "iloc") else frame[i]
        if cols:
            rows.append({str(k): _item(row.get(k)) for k in cols})
        else:
            # best-effort for row-like objects
            rows.append({str(k): _item(getattr(row, k)) for k in dir(row) if not k.startswith("_")})
    return rows


def _is_open_order_status(status: Any) -> bool:
    value = str(status or "").strip().upper()
    if not value:
        return False
    if value.startswith("FILLED"):
        return False
    if value.startswith("CANCEL"):
        return False
    if value in {"FAILED", "FAIL", "DISABLED", "DELETED", "EXPIRED"}:
        return False
    # Conservative default: treat other statuses as open/working.
    return True


@dataclass(frozen=True)
class RealAccountTarget:
    acc_id: int
    trd_env: str
    acc_type: str | None = None
    acc_status: str | None = None
    trdmarket_auth: Any | None = None
    security_firm: str | None = None


def _select_real_account(accounts: list[dict[str, Any]]) -> RealAccountTarget | None:
    # Prefer REAL over SIMULATE; if multiple REAL accounts exist, take the first ACTIVE one.
    # moomoo may return enum-like strings such as "TrdEnv.REAL"; accept any value that contains "REAL".
    real = [a for a in accounts if "REAL" in str(a.get("trd_env", "")).upper()]
    if not real:
        return None
    active = [a for a in real if str(a.get("acc_status", "")).upper() == "ACTIVE"]
    pick = active[0] if active else real[0]
    return RealAccountTarget(
        acc_id=int(pick.get("acc_id") or 0),
        trd_env=str(pick.get("trd_env") or "REAL"),
        acc_type=str(pick.get("acc_type") or "") or None,
        acc_status=str(pick.get("acc_status") or "") or None,
        trdmarket_auth=pick.get("trdmarket_auth"),
        security_firm=str(pick.get("security_firm") or "") or None,
    )


def fetch_real_account_snapshot(
    *,
    host: str = "127.0.0.1",
    port: int = 11111,
    security_firm: str = "FUTUINC",
    expected_trdmarket: str = "US",
) -> dict[str, Any]:
    """
    Read-only real account snapshot via moomoo OpenD.

    Safety:
    - Does NOT call unlock_trade.
    - Does NOT place/cancel/modify any order.
    """
    OpenSecTradeContext, RET_OK, SecurityFirm, TrdEnv, TrdMarket = _prepare_moomoo_imports()
    firm_value = getattr(SecurityFirm, security_firm, SecurityFirm.FUTUINC)
    # Important: restrict to the expected trading market; otherwise OpenD may only return
    # SIMULATE/HK accounts even when a US/REAL account exists.
    ctx = OpenSecTradeContext(host=host, port=port, security_firm=firm_value, filter_trdmarket=TrdMarket.US)
    try:
        ret, acc_frame = ctx.get_acc_list()
        if ret != RET_OK:
            return {
                "status": "FAIL",
                "timestamp": now_iso(),
                "source": "moomoo OpenD OpenSecTradeContext.get_acc_list",
                "host": host,
                "port": port,
                "securityFirm": str(firm_value),
                "error": str(acc_frame),
                "accounts": [],
            }

        accounts = _frame_to_records(acc_frame)
        target = _select_real_account(accounts)
        if target is None:
            return {
                "status": "NO_REAL_ACCOUNT",
                "timestamp": now_iso(),
                "source": "moomoo OpenD OpenSecTradeContext",
                "host": host,
                "port": port,
                "securityFirm": str(firm_value),
                "expectedTrdMarket": expected_trdmarket,
                "accounts": accounts,
                "note": "OpenD returned no REAL accounts. Ensure moomoo OpenD is logged into the REAL account and trade API permission is enabled.",
            }

        # Best-effort: only proceed if the account authorizes the expected market.
        trdmarket_auth = str(target.trdmarket_auth or "")
        if expected_trdmarket and expected_trdmarket.upper() not in trdmarket_auth.upper():
            return {
                "status": "REAL_ACCOUNT_MARKET_MISMATCH",
                "timestamp": now_iso(),
                "source": "moomoo OpenD OpenSecTradeContext",
                "host": host,
                "port": port,
                "securityFirm": str(firm_value),
                "expectedTrdMarket": expected_trdmarket,
                "accounts": accounts,
                "selectedAccount": target.__dict__,
                "note": f"REAL account does not show {expected_trdmarket} in trdmarket_auth.",
            }

        # Query account info / positions / open orders. Keep refresh_cache=False for safety; allow override via env.
        refresh_cache = os.environ.get("MOOMOO_REAL_REFRESH_CACHE", "0").strip() == "1"
        try:
            ret, accinfo = ctx.accinfo_query(trd_env=TrdEnv.REAL, acc_id=target.acc_id, refresh_cache=refresh_cache, currency="USD")
        except TypeError:
            # Some moomoo SDK builds don't support the currency kwarg.
            ret, accinfo = ctx.accinfo_query(trd_env=TrdEnv.REAL, acc_id=target.acc_id, refresh_cache=refresh_cache)
        if ret != RET_OK:
            accinfo_records: list[dict[str, Any]] = []
            accinfo_error = str(accinfo)
        else:
            accinfo_records = _frame_to_records(accinfo)
            accinfo_error = None

        try:
            ret, positions = ctx.position_list_query(trd_env=TrdEnv.REAL, acc_id=target.acc_id, refresh_cache=refresh_cache, currency="USD")
        except TypeError:
            # Some moomoo SDK builds don't support the currency kwarg.
            ret, positions = ctx.position_list_query(trd_env=TrdEnv.REAL, acc_id=target.acc_id, refresh_cache=refresh_cache)
        if ret != RET_OK:
            position_records: list[dict[str, Any]] = []
            positions_error = str(positions)
        else:
            position_records = _frame_to_records(positions)
            positions_error = None

        ret, orders = ctx.order_list_query(trd_env=TrdEnv.REAL, acc_id=target.acc_id, refresh_cache=refresh_cache)
        if ret != RET_OK:
            order_records: list[dict[str, Any]] = []
            orders_error = str(orders)
        else:
            order_records = _frame_to_records(orders)
            orders_error = None
        open_order_records = [r for r in order_records if _is_open_order_status(r.get("order_status"))]

        return {
            "status": "PASS" if not (accinfo_error or positions_error or orders_error) else "PARTIAL",
            "timestamp": now_iso(),
            "source": "moomoo OpenD OpenSecTradeContext (read-only)",
            "host": host,
            "port": port,
            "securityFirm": str(firm_value),
            "selectedAccount": target.__dict__,
            "accounts": accounts,
            "refreshCache": refresh_cache,
            "accinfo": {"records": accinfo_records, "error": accinfo_error},
            "positions": {"records": position_records, "error": positions_error},
            "orders": {"records": open_order_records, "all_records": order_records, "error": orders_error},
            "policy": {
                "readOnly": True,
                "unlockTradeCalled": False,
                "orderPlacementCalled": False,
                "orderCancelCalled": False,
                "orderModifyCalled": False,
            },
        }
    finally:
        ctx.close()


def write_outputs(snapshot: dict[str, Any]) -> tuple[str, str]:
    data_dir = ROOT / "data" / "broker" / "moomoo"
    latest_path = data_dir / "real_account_latest.json"
    stamped_path = data_dir / f"{today_stamp()}_real_account_snapshot.json"
    write_json(latest_path, snapshot)
    write_json(stamped_path, snapshot)

    status = snapshot.get("status")
    selected = snapshot.get("selectedAccount") or {}
    position_count = len(((snapshot.get("positions") or {}).get("records") or []))
    order_count = len(((snapshot.get("orders") or {}).get("records") or []))
    lines = [
        "# Moomoo Real Account Snapshot (Read-only)",
        "",
        f"- Timestamp: {snapshot.get('timestamp')}",
        f"- Status: {status}",
        f"- Host: {snapshot.get('host')}:{snapshot.get('port')}",
        f"- Security firm: {snapshot.get('securityFirm')}",
        "",
        "## Selected Account",
        "",
        f"- acc_id: {selected.get('acc_id')}",
        f"- trd_env: {selected.get('trd_env')}",
        f"- acc_type: {selected.get('acc_type')}",
        f"- acc_status: {selected.get('acc_status')}",
        f"- trdmarket_auth: {selected.get('trdmarket_auth')}",
        "",
        "## Summary",
        "",
        f"- Positions: {position_count}",
        f"- Open orders: {order_count}",
        "",
        "## Policy",
        "",
        "- Read-only only. This script does not unlock trading or place/cancel/modify orders.",
    ]
    note = snapshot.get("note")
    if note:
        lines += ["", "## Note", "", f"- {note}"]
    report_path = write_report("moomoo_real_account_snapshot", lines)
    return str(report_path), str(latest_path)
