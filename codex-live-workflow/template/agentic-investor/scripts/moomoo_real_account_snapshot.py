from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_investor_common import ROOT, now_iso, write_json


@dataclass(frozen=True)
class MoomooConn:
    host: str
    port: int


def _mask_account_id(account_id: str | None) -> str | None:
    if not account_id:
        return None
    tail = account_id[-4:] if len(account_id) >= 4 else account_id
    return f"***{tail}"


def _to_records(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [dict(item) for item in data]
    if isinstance(data, dict):
        return [dict(data)]
    try:
        return [dict(data)]
    except Exception:
        return []


def _now_local_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def fetch_real_account_snapshot(
    conn: MoomooConn,
    prefer_broker: str = "FUTUINC",
    prefer_market: str = "US",
) -> dict[str, Any]:
    """
    Read-only REAL account snapshot via moomoo OpenD.

    This function must NOT unlock trading, place orders, modify orders, or cancel orders.
    """
    from moomoo import (  # type: ignore[import-not-found]
        OpenSecTradeContext,
        RET_OK,
        TrdMarket,
    )

    trade_ctx = OpenSecTradeContext(filter_trdmarket=TrdMarket.US, host=conn.host, port=conn.port)
    try:
        ret, acc_df = trade_ctx.get_acc_list()
        if ret != RET_OK:
            raise RuntimeError(f"get_acc_list failed: {acc_df}")

        accounts: list[dict[str, Any]] = acc_df.to_dict("records") if hasattr(acc_df, "to_dict") else _to_records(acc_df)
        if not accounts:
            raise RuntimeError("OpenD returned empty account list.")

        def _score(acc: dict[str, Any]) -> tuple[int, int, int]:
            broker = str(acc.get("broker_id") or "")
            market = str(acc.get("trdmarket") or acc.get("trd_market") or "")
            acc_type = str(acc.get("acc_type") or "")
            return (
                1 if prefer_broker and prefer_broker in broker else 0,
                1 if prefer_market and prefer_market in market else 0,
                1 if "MARGIN" in acc_type.upper() else 0,
            )

        selected = sorted(accounts, key=_score, reverse=True)[0]
        trd_env = selected.get("trd_env") or selected.get("trdEnv") or selected.get("trd_env_type") or selected.get("trdEnvType")
        acc_id = selected.get("acc_id") or selected.get("accID") or selected.get("accId")

        if not acc_id:
            raise RuntimeError(f"Selected account missing acc_id fields: {selected}")

        # Funds / account info
        ret, funds_df = trade_ctx.accinfo_query(trd_env=trd_env, acc_id=acc_id)
        if ret != RET_OK:
            raise RuntimeError(f"accinfo_query failed: {funds_df}")
        funds = funds_df.to_dict("records")[0] if hasattr(funds_df, "to_dict") and len(funds_df) else {}

        trading_info: dict[str, Any] = {}
        errors: dict[str, str] = {}
        # NOTE: moomoo OpenAPI's `acctradinginfo_query` is instrument-scoped (needs order_type/code/price).
        # We keep account-level buying power fields from `accinfo_query` and skip this by default.

        # Positions
        ret, pos_df = trade_ctx.position_list_query(trd_env=trd_env, acc_id=acc_id)
        if ret != RET_OK:
            raise RuntimeError(f"position_list_query failed: {pos_df}")
        positions = pos_df.to_dict("records") if hasattr(pos_df, "to_dict") else _to_records(pos_df)

        # Open orders (all statuses, caller can filter)
        ret, orders_df = trade_ctx.order_list_query(trd_env=trd_env, acc_id=acc_id)
        if ret != RET_OK:
            raise RuntimeError(f"order_list_query failed: {orders_df}")
        orders = orders_df.to_dict("records") if hasattr(orders_df, "to_dict") else _to_records(orders_df)

        snapshot = {
            "task": "moomoo_real_account_snapshot",
            "timestamp": _now_local_iso(),
            "status": "PASS",
            "source": "moomoo OpenD (REAL account read-only)",
            "connection": {"host": conn.host, "port": conn.port},
            "accountSelected": {
                "broker_id": selected.get("broker_id"),
                "trdmarket": selected.get("trdmarket") or selected.get("trd_market"),
                "acc_type": selected.get("acc_type"),
                "trd_env": trd_env,
                "acc_id_masked": _mask_account_id(str(acc_id)),
            },
            "funds": funds,
            "tradingInfo": trading_info,
            "positions": positions,
            "orders": orders,
            "errors": errors,
            "dataPolicy": {
                "read_only": True,
                "unlock_trade_called": False,
                "place_order_called": False,
                "modify_order_called": False,
                "cancel_order_called": False,
            },
        }
        return snapshot
    finally:
        try:
            trade_ctx.close()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only snapshot of moomoo REAL account via OpenD.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11111)
    parser.add_argument("--out", default=str(ROOT / "data" / "moomoo_real" / "latest.json"))
    args = parser.parse_args(argv)

    out_path = Path(args.out)
    conn = MoomooConn(host=args.host, port=args.port)
    snapshot = fetch_real_account_snapshot(conn)
    write_json(out_path, snapshot)
    print(json.dumps({"status": snapshot["status"], "timestamp": snapshot["timestamp"], "out": str(out_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
