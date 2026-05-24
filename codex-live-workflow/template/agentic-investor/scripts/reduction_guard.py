# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python always has this in supported runtimes.
    ZoneInfo = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACCOUNT_PATH = ROOT / "data" / "broker" / "moomoo" / "real_account_latest.json"
DEFAULT_MARKET_PATH = ROOT / "data" / "market" / "latest.json"
DEFAULT_JSON_OUT = ROOT / "data" / "risk" / "reduction_guard_latest.json"
DEFAULT_REPORT_DIR = ROOT / "reports"


def default_weixin_sender() -> Path:
    configured = os.environ.get("WEIXIN_SEND_SCRIPT") or os.environ.get("WECHAT_SEND_SCRIPT")
    if configured:
        return Path(configured).expanduser()
    home = Path(os.environ.get("USERPROFILE") or str(Path.home()))
    return home / "Documents" / "Codex" / "weixin-send.mjs"


WEIXIN_SEND = default_weixin_sender()

ACTION_ORDER = {
    "EXIT": 5,
    "TRIM": 4,
    "WATCH": 2,
    "HOLD": 1,
    "NO_POSITION": 0,
}


@dataclass
class Position:
    symbol: str
    code: str
    qty: float
    cost: float
    snapshot_price: float | None
    snapshot_pl: float | None
    can_sell_qty: float | None


def tz(name: str, fallback_hours: int) -> timezone:
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)  # type: ignore[return-value]
        except Exception:
            pass
    return timezone(timedelta(hours=fallback_hours))


CN_TZ = tz("Asia/Shanghai", 8)
NY_TZ = tz("America/New_York", -4)


def now_cn() -> datetime:
    return datetime.now(timezone.utc).astimezone(CN_TZ)


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fnum(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "N/A":
            return default
        result = float(value)
        if math.isfinite(result):
            return result
    except (TypeError, ValueError):
        pass
    return default


def fmt_price(value: Any) -> str:
    number = fnum(value)
    return "N/A" if number is None else f"{number:.2f}"


def fmt_pct(value: Any) -> str:
    number = fnum(value)
    return "N/A" if number is None else f"{number:.2f}%"


def symbol_of(code: str) -> str:
    return str(code or "").split(".")[-1].upper()


def normalize_account(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("accinfo", {}).get("records") is not None:
        acc = (raw.get("accinfo", {}).get("records") or [{}])[0]
        raw_positions = raw.get("positions", {}).get("records") or []
        raw_orders = raw.get("orders", {}).get("records") or []
        selected = raw.get("selectedAccount") or {}
        currency = acc.get("currency") or "USD"
        timestamp = raw.get("timestamp")
        source = raw.get("source") or "data/broker/moomoo/real_account_latest.json"
    else:
        acc = raw.get("funds") or {}
        raw_positions = raw.get("positions") or []
        raw_orders = raw.get("orders") or []
        selected = raw.get("accountSelected") or {}
        currency = acc.get("currency") or "USD"
        timestamp = raw.get("timestamp")
        source = raw.get("source") or "legacy moomoo snapshot"

    positions: list[Position] = []
    for item in raw_positions:
        code = str(item.get("code") or item.get("symbol") or "")
        symbol = symbol_of(code)
        if not symbol:
            continue
        qty = fnum(item.get("qty"), 0.0) or 0.0
        cost = fnum(item.get("cost_price") or item.get("average_cost"), 0.0) or 0.0
        snapshot_price = fnum(item.get("nominal_price") or item.get("last") or item.get("market_price"))
        positions.append(
            Position(
                symbol=symbol,
                code=code,
                qty=qty,
                cost=cost,
                snapshot_price=snapshot_price,
                snapshot_pl=fnum(item.get("pl_val") or item.get("unrealized_pl")),
                can_sell_qty=fnum(item.get("can_sell_qty")),
            )
        )

    return {
        "timestamp": timestamp,
        "source": source,
        "selected_account": selected,
        "cash": fnum(acc.get("cash") or acc.get("us_cash")),
        "total_assets": fnum(acc.get("total_assets") or acc.get("usd_assets")),
        "market_val": fnum(acc.get("market_val") or acc.get("long_mv")),
        "buying_power": fnum(acc.get("power")),
        "risk_status": acc.get("risk_status") or "N/A",
        "currency": currency,
        "positions": positions,
        "open_orders": raw_orders,
        "raw_position_count": len(raw_positions),
    }


def market_rows(node: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(node, list):
        for item in node:
            rows.extend(market_rows(item))
    elif isinstance(node, dict):
        if "symbol" in node and ("last" in node or "value" in node):
            rows.append(node)
        for value in node.values():
            if isinstance(value, (dict, list)):
                rows.extend(market_rows(value))
    return rows


def quote_from_market_snapshot(market: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    wanted = symbol.upper()
    for row in market_rows(market):
        row_symbol = symbol_of(str(row.get("symbol") or ""))
        if row_symbol != wanted:
            continue
        price = fnum(row.get("last") if "last" in row else row.get("value"))
        if price is None:
            continue
        return {
            "symbol": wanted,
            "price": price,
            "day_change_pct": fnum(row.get("dayChangePct") or row.get("day_change_pct")),
            "as_of": row.get("asOf") or market.get("timestamp"),
            "source": "data/market/latest.json",
        }
    return None


def valid_closes(values: Any) -> list[float]:
    closes: list[float] = []
    if not isinstance(values, list):
        return closes
    for item in values:
        number = fnum(item)
        if number is not None:
            closes.append(number)
    return closes


def fetch_yahoo_quote(symbol: str, interval: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(symbol, safe="")
    if interval == "1m":
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=1d&interval=1m"
    else:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=5d&interval=1d"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(str(chart["error"]))
    result = (chart.get("result") or [None])[0]
    if not result:
        raise RuntimeError("Yahoo chart returned no result")
    meta = result.get("meta") or {}
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = valid_closes(quote.get("close"))
    timestamps = result.get("timestamp") or []
    if not closes:
        price = fnum(meta.get("regularMarketPrice"))
        if price is None:
            raise RuntimeError("Yahoo chart returned no close rows")
    else:
        price = closes[-1]
    previous = fnum(meta.get("chartPreviousClose") or meta.get("previousClose"))
    if previous is None and len(closes) >= 2:
        previous = closes[-2]
    day_change_pct = ((price - previous) / previous * 100) if previous else None
    as_of = None
    if timestamps:
        try:
            as_of = datetime.fromtimestamp(int(timestamps[-1]), timezone.utc).isoformat(timespec="seconds")
        except Exception:
            as_of = None
    return {
        "symbol": symbol.upper().replace("^", ""),
        "price": price,
        "day_change_pct": day_change_pct,
        "as_of": as_of,
        "source": f"Yahoo chart {interval}",
        "url": url,
    }


def load_quote(symbol: str, market: dict[str, Any], fallback_price: float | None, no_yahoo: bool) -> dict[str, Any]:
    if not no_yahoo:
        for interval in ("1m", "1d"):
            try:
                return fetch_yahoo_quote(symbol, interval)
            except Exception:
                continue
    snapshot = quote_from_market_snapshot(market, symbol)
    if snapshot:
        return snapshot
    if fallback_price is not None:
        return {
            "symbol": symbol,
            "price": fallback_price,
            "day_change_pct": None,
            "as_of": None,
            "source": "account snapshot nominal_price fallback",
        }
    return {
        "symbol": symbol,
        "price": None,
        "day_change_pct": None,
        "as_of": None,
        "source": "missing",
    }


def session_phase() -> dict[str, Any]:
    ny_now = datetime.now(timezone.utc).astimezone(NY_TZ)
    weekday = ny_now.weekday()
    open_time = time(9, 30)
    close_time = time(16, 0)
    if weekday >= 5 or not (open_time <= ny_now.time() <= close_time):
        return {
            "phase": "closed_or_latest_close",
            "ny_time": ny_now.isoformat(timespec="seconds"),
            "minutes_since_open": None,
        }
    open_dt = ny_now.replace(hour=9, minute=30, second=0, microsecond=0)
    minutes = int((ny_now - open_dt).total_seconds() // 60)
    if minutes < 30:
        phase = "opening_wait_30m"
    elif minutes < 60:
        phase = "opening_confirmation_30_60m"
    else:
        phase = "regular_after_60m"
    return {
        "phase": phase,
        "ny_time": ny_now.isoformat(timespec="seconds"),
        "minutes_since_open": minutes,
    }


def action(symbol: str, position: Position, quote: dict[str, Any], phase: str) -> dict[str, Any]:
    price = fnum(quote.get("price"))
    if price is None:
        return decision(symbol, position, quote, "WATCH", "缺少可用价格，先人工核对", "不下单", None)

    cost = position.cost
    pnl = (price - cost) * position.qty if cost else None
    pnl_pct = ((price / cost - 1) * 100) if cost else None

    def d(act: str, reason: str, size: str, trigger: str | None = None) -> dict[str, Any]:
        return decision(symbol, position, quote, act, reason, size, trigger, pnl, pnl_pct)

    opening_wait = phase == "opening_wait_30m"

    if symbol == "CBRS":
        if price < 250:
            return d("EXIT", "CBRS 跌破 250 硬止损，IPO 高 beta 继续扩散风险", "卖出全部可卖数量", "<250")
        if opening_wait and price < 275:
            return d("WATCH", "开盘前 30 分钟内低于 270-275 修复带，等确认但提前预警", "暂不自动动作", "<275 before 30m")
        if price < 270:
            return d("EXIT", "未收复 270-275，且已经跌破 270，弱势确认", "卖出全部可卖数量", "<270")
        if price < 275:
            return d("TRIM", "仍在 270-275 修复带下沿，反弹质量不够", "至少减半；只有 1 股则卖出 1 股", "270-275")
        if price < 285:
            return d("WATCH", "低于 285 修复确认位，禁止加仓，继续观察", "不加仓", "<285")
        return d("HOLD", "已回到修复区上方，继续观察能否收复成本 301.91", "持有", ">=285")

    if symbol == "RDDT":
        if price < 140:
            return d("EXIT", "RDDT 跌破 140，弱势延续，不再等反抽", "卖出全部可卖数量", "<140")
        if opening_wait and price < 145:
            return d("WATCH", "开盘前 30 分钟内未收回 144-145，等确认但提前预警", "暂不自动动作", "<145 before 30m")
        if price < 144:
            return d("EXIT", "未收回 144-145 修复线，价格继续不认可基本面", "卖出全部可卖数量", "<144")
        if price < 150:
            return d("TRIM", "144-150 之间只是弱修复，先降低亏损仓暴露", "至少减半；只有 1 股则卖出 1 股", "144-150")
        return d("HOLD", "站回 150 上方才算从止损仓回到观察仓", "持有", ">=150")

    if symbol == "NVDA":
        if price < 211:
            return d("TRIM", "NVDA 跌破 211，核心仓也要保护利润", "卖出 1 股或约三分之一仓位", "<211")
        if price < 221:
            return d("WATCH", "低于 221-223 修复带，不加仓；若收盘仍无法修复再减 1 股", "不加仓，准备减 1 股", "<221")
        return d("HOLD", "仍在核心持有区，财报主线未破", "持有", ">=221")

    if symbol == "RKLB":
        if price < 128:
            return d("TRIM", "RKLB 跌破 128，Space 催化后的利润保护触发", "卖出 1 股或至少锁定利润", "<128")
        if price < 130:
            return d("WATCH", "128-130 是回踩确认区，不追高也不加仓", "持有观察", "128-130")
        return d("HOLD", "RKLB 在 130 上方，Space Force 催化仍有效", "持有", ">=130")

    if symbol == "CORZ":
        if price < 23.8:
            return d("EXIT", "CORZ 跌破 23.8 防守线，AI miner 修复失败", "卖出全部可卖数量", "<23.8")
        if price < 24.0:
            return d("TRIM", "CORZ 贴近 23.8-24.0 防守区，先降风险", "至少减半", "23.8-24.0")
        return d("HOLD", "CORZ 仍在防守线上方，25.5 以上才考虑加仓", "持有", ">=24")

    if symbol == "DRAM":
        if price < 50.5:
            return d("TRIM", "DRAM 跌破 50.5，存储 beta 重新转弱", "减半", "<50.5")
        if price < 52:
            return d("WATCH", "DRAM 低于成本附近，暂不加仓", "持有观察", "<52")
        return d("HOLD", "DRAM 仍在成本线上方", "持有", ">=52")

    if symbol == "ORCL":
        if price < 180:
            return d("TRIM", "ORCL 跌破 180，弱于软件/AI 基建主线", "卖出 1 股", "<180")
        if price < 188:
            return d("WATCH", "ORCL 低于修复区，继续观察", "不加仓", "<188")
        return d("HOLD", "ORCL 尚未触发减仓线", "持有", ">=188")

    if pnl_pct is not None and pnl_pct <= -15:
        return d("EXIT", "通用风控：单票亏损超过 15%", "卖出全部可卖数量", "<=-15%")
    if pnl_pct is not None and pnl_pct <= -10:
        return d("TRIM", "通用风控：单票亏损超过 10%", "至少减半", "<=-10%")
    if pnl_pct is not None and pnl_pct <= -7:
        return d("WATCH", "通用风控：单票亏损超过 7%，需要人工复核", "不加仓", "<=-7%")
    return d("HOLD", "未触发减仓条件", "持有", None)


def decision(
    symbol: str,
    position: Position,
    quote: dict[str, Any],
    act: str,
    reason: str,
    size: str,
    trigger: str | None,
    pnl: float | None = None,
    pnl_pct: float | None = None,
) -> dict[str, Any]:
    price = fnum(quote.get("price"))
    if pnl is None and price is not None and position.cost:
        pnl = (price - position.cost) * position.qty
    if pnl_pct is None and price is not None and position.cost:
        pnl_pct = (price / position.cost - 1) * 100
    return {
        "symbol": symbol,
        "qty": position.qty,
        "can_sell_qty": position.can_sell_qty,
        "cost": position.cost,
        "last": price,
        "day_change_pct": fnum(quote.get("day_change_pct")),
        "market_value": price * position.qty if price is not None else None,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "action": act,
        "severity": ACTION_ORDER.get(act, 0),
        "size_suggestion": size,
        "trigger": trigger,
        "reason": reason,
        "quote_source": quote.get("source"),
        "quote_as_of": quote.get("as_of"),
    }


def build_report(result: dict[str, Any]) -> str:
    lines = [
        "# moomoo reduction guard",
        "",
        f"- Generated: {result['generated_at_cn']}",
        f"- Account snapshot: {result['account']['timestamp']}",
        f"- Account source: {result['account']['source']}",
        f"- Session phase: {result['session']['phase']}",
        f"- Data policy: read-only; no unlock; no place/cancel/modify orders",
        "",
        "## Summary",
        "",
        f"- Conclusion: {result['summary']['conclusion']}",
        f"- Actionable count: {result['summary']['actionable_count']}",
        f"- Estimated total assets: {fmt_price(result['summary'].get('estimated_total_assets'))}",
        f"- Cash: {fmt_price(result['account'].get('cash'))}",
        f"- Risk status: {result['account'].get('risk_status')}",
        "",
        "## Position checks",
        "",
        "| Symbol | Qty | Cost | Last | P/L | P/L % | Action | Size | Reason |",
        "|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for row in result["positions"]:
        lines.append(
            "| {symbol} | {qty:.2f} | {cost} | {last} | {pnl} | {pnl_pct} | {action} | {size} | {reason} |".format(
                symbol=row["symbol"],
                qty=fnum(row["qty"], 0.0) or 0.0,
                cost=fmt_price(row["cost"]),
                last=fmt_price(row["last"]),
                pnl=fmt_price(row["pnl"]),
                pnl_pct=fmt_pct(row["pnl_pct"]),
                action=row["action"],
                size=str(row["size_suggestion"]).replace("|", "/"),
                reason=str(row["reason"]).replace("|", "/"),
            )
        )
    lines.extend(
        [
            "",
            "## Hard rules encoded",
            "",
            "- CBRS: <250 exit; <270 exit after opening confirmation; 270-275 trim; <285 watch.",
            "- RDDT: <140 exit; <144 exit after opening confirmation; 144-150 trim; >=150 repaired watch.",
            "- NVDA: <211 trim one share; <221 watch/no add; >=221 core hold.",
            "- RKLB: <128 trim/take profit; 128-130 watch; >=130 hold.",
            "- CORZ: <23.8 exit; 23.8-24.0 trim; >=24 hold.",
            "- Generic: <=-15% exit; <=-10% trim; <=-7% review.",
            "",
            "## Execution",
            "",
            "This script is advisory-only. It never unlocks trading and never sends broker orders.",
            "",
        ]
    )
    return "\n".join(lines)


def build_wechat_message(result: dict[str, Any]) -> str:
    actionable = [row for row in result["positions"] if row["action"] in {"EXIT", "TRIM"}]
    watch = [row for row in result["positions"] if row["action"] == "WATCH"]
    lines = [
        "【moomoo减仓守卫】",
        f"结论：{result['summary']['conclusion']}",
        f"账户快照：{result['account']['timestamp']}；脚本生成：{result['generated_at_cn']}",
        "只读提醒：不会自动下单/撤单/改单。",
        "",
    ]
    if actionable:
        lines.append("需要处理：")
        for row in actionable[:6]:
            lines.append(
                f"- {row['symbol']} {row['action']}：现价 {fmt_price(row['last'])}，盈亏 {fmt_price(row['pnl'])} ({fmt_pct(row['pnl_pct'])})；{row['size_suggestion']}；原因：{row['reason']}"
            )
    else:
        lines.append("当前没有硬性减仓触发。")
    if watch:
        lines.append("")
        lines.append("观察预警：")
        for row in watch[:6]:
            lines.append(f"- {row['symbol']} WATCH：现价 {fmt_price(row['last'])}；{row['reason']}")
    lines.extend(
        [
            "",
            f"现金：{fmt_price(result['account'].get('cash'))}；估算总资产：{fmt_price(result['summary'].get('estimated_total_assets'))}；风险状态：{result['account'].get('risk_status')}",
            f"报告：{result['report_path']}",
        ]
    )
    return "\n".join(lines)


def send_wechat(message: str) -> dict[str, Any]:
    if not WEIXIN_SEND.exists():
        return {"ok": False, "error": f"missing {WEIXIN_SEND}"}
    proc = subprocess.run(
        ["node", str(WEIXIN_SEND), message],
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only moomoo reduction guard.")
    parser.add_argument("--account", type=Path, default=DEFAULT_ACCOUNT_PATH)
    parser.add_argument("--market", type=Path, default=DEFAULT_MARKET_PATH)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--no-yahoo", action="store_true", help="Skip Yahoo quote refresh and use local files only.")
    parser.add_argument("--push", action="store_true", help="Push actionable results to WeChat.")
    parser.add_argument("--force-push", action="store_true", help="Push even when there is no EXIT/TRIM action.")
    parser.add_argument("--include", default="", help="Comma-separated extra symbols to quote, for diagnostics only.")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    account_raw = read_json(args.account)
    market_raw = read_json(args.market, {})
    account = normalize_account(account_raw)
    phase = session_phase()

    quotes: dict[str, dict[str, Any]] = {}
    positions: list[Position] = account["positions"]
    for position in positions:
        quotes[position.symbol] = load_quote(position.symbol, market_raw, position.snapshot_price, args.no_yahoo)

    extra_symbols = [item.strip().upper() for item in args.include.split(",") if item.strip()]
    diagnostics = {
        symbol: load_quote(symbol, market_raw, None, args.no_yahoo)
        for symbol in extra_symbols
        if symbol not in quotes
    }

    rows = [action(position.symbol, position, quotes[position.symbol], phase["phase"]) for position in positions]
    rows.sort(key=lambda item: (-int(item["severity"]), item["symbol"]))

    current_long_value = sum(fnum(row.get("market_value"), 0.0) or 0.0 for row in rows)
    cash = fnum(account.get("cash"), 0.0) or 0.0
    estimated_total = cash + current_long_value if current_long_value else account.get("total_assets")
    actionable_count = sum(1 for row in rows if row["action"] in {"EXIT", "TRIM"})
    conclusion = (
        f"触发 {actionable_count} 个减仓/清仓动作，优先处理最上方标的。"
        if actionable_count
        else "没有触发硬性减仓；继续观察 WATCH 项。"
    )

    result: dict[str, Any] = {
        "task": "moomoo_reduction_guard",
        "generated_at_cn": now_cn().isoformat(timespec="seconds"),
        "account": {
            "timestamp": account["timestamp"],
            "source": account["source"],
            "selected_account": account["selected_account"],
            "cash": account["cash"],
            "total_assets": account["total_assets"],
            "market_val": account["market_val"],
            "buying_power": account["buying_power"],
            "risk_status": account["risk_status"],
            "currency": account["currency"],
            "position_count": len(positions),
            "open_order_count": len(account["open_orders"]),
        },
        "session": phase,
        "summary": {
            "conclusion": conclusion,
            "actionable_count": actionable_count,
            "estimated_current_long_value": current_long_value,
            "estimated_total_assets": estimated_total,
        },
        "positions": rows,
        "diagnostic_quotes": diagnostics,
        "data_policy": {
            "read_only": True,
            "unlock_trade_called": False,
            "place_order_called": False,
            "cancel_order_called": False,
            "modify_order_called": False,
        },
    }

    report_path = args.report_dir / f"{now_cn().strftime('%Y-%m-%d')}_moomoo_reduction_guard.md"
    result["report_path"] = str(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report(result), encoding="utf-8")
    write_json(args.json_out, result)

    push_result = None
    if args.push and (actionable_count or args.force_push):
        push_result = send_wechat(build_wechat_message(result))
        result["wechat_push"] = push_result
        write_json(args.json_out, result)

    print(
        json.dumps(
            {
                "status": "ACTION" if actionable_count else "NO_ACTION",
                "summary": result["summary"],
                "report": str(report_path),
                "json": str(args.json_out),
                "wechat_push": push_result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
