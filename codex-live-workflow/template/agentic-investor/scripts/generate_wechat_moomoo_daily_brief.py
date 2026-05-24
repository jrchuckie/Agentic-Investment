from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_investor_common import ROOT, now_iso, read_json, today_stamp, write_json


def _fmt_num(x: Any, digits: int = 2) -> str:
    if x is None:
        return "N/A"
    try:
        v = float(x)
    except Exception:
        return str(x)
    if not math.isfinite(v):
        return "N/A"
    if abs(v) >= 1000:
        return f"{v:,.{digits}f}"
    return f"{v:.{digits}f}"


def _fmt_pct(x: Any, digits: int = 2) -> str:
    if x is None:
        return "N/A"
    try:
        v = float(x)
    except Exception:
        return str(x)
    if not math.isfinite(v):
        return "N/A"
    return f"{v:.{digits}f}%"


def _safe_float(x: Any) -> float | None:
    try:
        v = float(x)
    except Exception:
        return None
    if not math.isfinite(v):
        return None
    return v


def _dt_local(ts: str | None) -> str:
    if not ts:
        return "N/A"
    return ts


@dataclass(frozen=True)
class BriefPaths:
    account_snapshot: Path
    market_snapshot: Path
    social_snapshot: Path
    watchlist: Path
    intel_snapshot: Path | None = None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _pick_macro(true_series: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    for item in true_series:
        if item.get("symbol") == symbol:
            return item
    return None


def _summarize_positions(positions: list[dict[str, Any]]) -> dict[str, Any]:
    enriched = []
    total_mv = 0.0
    for row in positions:
        mv = _safe_float(row.get("market_val")) or 0.0
        total_mv += mv
        enriched.append({**row, "_mv": mv})
    enriched.sort(key=lambda x: x.get("_mv", 0.0), reverse=True)
    for row in enriched:
        row["_weight"] = (row.get("_mv", 0.0) / total_mv * 100.0) if total_mv > 0 else None
    return {"total_market_val": total_mv, "positions_sorted": enriched}


def _order_is_open(order: dict[str, Any]) -> bool:
    status = str(order.get("order_status") or "").upper()
    return status not in {"FILLED_ALL", "CANCELLED_ALL", "FAILED", "DELETED"}


def _pick_latest_intel_snapshot() -> Path | None:
    intel_dir = ROOT / "data" / "intelligence"
    if not intel_dir.exists():
        return None
    candidates = sorted(intel_dir.glob("*_intel.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _summarize_intel(intel: dict[str, Any]) -> list[dict[str, Any]]:
    items = list(intel.get("items") or [])
    items.sort(key=lambda x: _safe_float(x.get("score")) or 0.0, reverse=True)
    top: list[dict[str, Any]] = []
    for it in items:
        title = str(it.get("title") or "").strip()
        url = str(it.get("url") or "").strip()
        if not title or not url:
            continue
        symbols = list(it.get("symbol_hits") or [])
        top.append(
            {
                "title": title,
                "url": url,
                "symbols": symbols[:6],
                "published_at": it.get("published_at"),
                "score": _safe_float(it.get("score")),
            }
        )
        if len(top) >= 5:
            break
    return top


def _suggest_actions(
    positions_sorted: list[dict[str, Any]],
    total_assets_hkd: float | None,
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for pos in positions_sorted[:6]:
        code = str(pos.get("code") or "")
        name = str(pos.get("stock_name") or "").strip() or code
        qty = _safe_float(pos.get("qty")) or 0.0
        can_sell = _safe_float(pos.get("can_sell_qty")) or 0.0
        cost = _safe_float(pos.get("cost_price"))
        last = _safe_float(pos.get("nominal_price")) or _safe_float(pos.get("market_val"))
        pl_ratio = _safe_float(pos.get("pl_ratio"))
        today_pl = _safe_float(pos.get("today_pl_val"))
        mv = _safe_float(pos.get("_mv")) or 0.0
        weight = _safe_float(pos.get("_weight"))

        action = "持有/观察"
        reasons: list[str] = []
        trigger: str | None = None
        invalidation: str | None = None
        sizing_hkd: str | None = None

        if pl_ratio is not None and pl_ratio <= -6:
            action = "减仓/止损"
            reasons = ["浮亏扩大，先控制回撤", "仓位较小但避免情绪化加仓摊平", "等待价格重新站回关键均线再评估"]
            if last:
                trigger = f"跌破 {last*0.97:.2f}（现价下方约3%）"
            if cost:
                invalidation = f"若重新站上成本价 {cost:.2f} 且量能改善，可取消止损计划"
        elif pl_ratio is not None and pl_ratio >= 10:
            action = "持有为主/可小幅止盈"
            reasons = ["趋势向上且已有浮盈", "避免一次性卖光错失延续行情", "用回撤止盈保护收益"]
            if last:
                trigger = f"回撤到 {last*0.95:.2f}（现价下方约5%）触发分批止盈"
            invalidation = "若指数出现VIX急升或10Y上行冲击成长股，优先降低波动暴露"
        else:
            action = "持有/等待信号"
            reasons = ["当前仓位轻，优先观察市场节奏", "等更清晰的趋势/催化再加码", "避免在高波动时追涨杀跌"]
            if last:
                trigger = f"突破 {last*1.03:.2f}（现价上方约3%）且大盘不转弱"
            invalidation = "若大盘转入明显风险-off（VIX上行、长端利率上行），暂停加仓"

        if total_assets_hkd and total_assets_hkd > 0:
            # Advisory sizing: cap single-name adds at ~5% of total assets (HKD base), keep it simple.
            sizing_hkd = f"新增不超过约 {_fmt_num(total_assets_hkd*0.05,0)} HKD（单一标的不超过5%）"

        suggestions.append(
            {
                "code": code,
                "name": name,
                "action": action,
                "qty": qty,
                "can_sell_qty": can_sell,
                "market_val_usd": mv,
                "weight_pct_of_us_positions": weight,
                "pl_ratio_pct": pl_ratio,
                "today_pl_usd": today_pl,
                "trigger": trigger,
                "invalidation": invalidation,
                "sizing_hkd": sizing_hkd,
                "reasons_top3": reasons[:3],
            }
        )
    return suggestions


def build_brief(paths: BriefPaths) -> dict[str, Any]:
    acct = _load_json(paths.account_snapshot)
    market = _load_json(paths.market_snapshot)
    social = _load_json(paths.social_snapshot)
    watchlist = _load_json(paths.watchlist)
    intel = _load_json(paths.intel_snapshot) if paths.intel_snapshot and paths.intel_snapshot.exists() else None

    funds = acct.get("funds") or {}
    positions = acct.get("positions") or []
    orders = acct.get("orders") or []

    pos_summary = _summarize_positions(list(positions))
    positions_sorted = pos_summary["positions_sorted"]

    total_assets_hkd = _safe_float(funds.get("total_assets"))
    cash_hkd = _safe_float(funds.get("cash"))
    power_hkd = _safe_float(funds.get("power"))
    risk_status = funds.get("risk_status") or funds.get("risk_level") or "N/A"
    is_pdt = bool(funds.get("is_pdt", False))
    pdt_seq = funds.get("pdt_seq")

    indices = market.get("indices") or []
    true_series = market.get("trueMacroSeries") or []
    macro_vix = _pick_macro(true_series, "VIX")
    macro_10y = _pick_macro(true_series, "DGS10")
    macro_usdcnh = _pick_macro(true_series, "USDCNH")

    macro_proxies = {item.get("symbol"): item for item in (market.get("macroProxies") or [])}
    vix_proxy = macro_proxies.get("VIXY")
    tlt = macro_proxies.get("TLT")
    uup = macro_proxies.get("UUP")

    open_orders = [o for o in orders if _order_is_open(o)]

    highlights = social.get("highlights") or []
    top_highlights = []
    for item in highlights[:5]:
        title = (item.get("titleZh") or item.get("title") or "").strip()
        if not title:
            continue
        top_highlights.append(
            {
                "title": title,
                "symbols": item.get("symbols") or [],
                "source": item.get("source") or item.get("sourceName"),
            }
        )

    suggestions = _suggest_actions(positions_sorted, total_assets_hkd)
    intel_highlights = _summarize_intel(intel) if isinstance(intel, dict) else []

    return {
        "task": "wechat_moomoo_daily_brief",
        "timestamp": now_iso(),
        "inputs": {
            "account_snapshot": str(paths.account_snapshot),
            "market_snapshot": str(paths.market_snapshot),
            "social_snapshot": str(paths.social_snapshot),
            "watchlist": str(paths.watchlist),
            "intel_snapshot": str(paths.intel_snapshot) if paths.intel_snapshot else None,
        },
        "account": {
            "timestamp": acct.get("timestamp"),
            "acc_id_masked": (acct.get("accountSelected") or {}).get("acc_id_masked"),
            "currency": funds.get("currency") or "N/A",
            "total_assets": total_assets_hkd,
            "cash": cash_hkd,
            "buying_power": power_hkd,
            "risk_status": risk_status,
            "initial_margin": _safe_float(funds.get("initial_margin")),
            "maintenance_margin": _safe_float(funds.get("maintenance_margin")),
            "margin_call_margin": _safe_float(funds.get("margin_call_margin")),
            "us_cash": _safe_float(funds.get("us_cash")),
            "usd_assets": _safe_float(funds.get("usd_assets")),
            "is_pdt": is_pdt,
            "pdt_seq": pdt_seq,
            "open_orders_count": len(open_orders),
            "orders_count_total": len(orders),
        },
        "positions": {
            "count": len(positions_sorted),
            "total_market_val_usd": pos_summary["total_market_val"],
            "top": positions_sorted[:10],
        },
        "market": {
            "timestamp": market.get("timestamp"),
            "indices": indices,
            "vix": macro_vix,
            "us10y": macro_10y,
            "usdcnh": macro_usdcnh,
            "vixy": vix_proxy,
            "tlt": tlt,
            "uup": uup,
        },
        "sentiment": {
            "timestamp": social.get("timestamp"),
            "marketMood": social.get("marketMood"),
            "crowding": social.get("crowding"),
            "highlights": top_highlights,
        },
        "intel": {
            "timestamp": (intel or {}).get("timestamp") if isinstance(intel, dict) else None,
            "highlights": intel_highlights,
        },
        "watchlist": {
            "count": len(watchlist.get("items") or []),
            "notes": "watchlist仅作参考，实盘以moomoo持仓为准",
        },
        "suggestions": suggestions,
        "open_orders": open_orders,
        "dataPolicy": {
            "read_only": True,
            "no_trading": True,
        },
    }


def render_wechat_text(data: dict[str, Any]) -> str:
    acct = data["account"]
    market = data["market"]
    positions = data["positions"]
    sentiment = data["sentiment"]
    intel = data.get("intel") or {}
    suggestions = data["suggestions"]
    open_orders = data.get("open_orders") or []

    lines: list[str] = []
    lines.append(f"【Moomoo 实盘每日投资简报】{today_stamp()}（北京时间）")
    lines.append(f"生成时间：{data['timestamp']}")
    try:
        local_now = datetime.now(timezone.utc).astimezone()
        if local_now.weekday() >= 5:
            lines.append(
                f"注：今天（{local_now.date().isoformat()}）为周末，美股休市；以下复盘/行情为最新可得（通常为上一交易日收盘/盘后数据）。"
            )
    except Exception:
        pass
    lines.append("")
    lines.append("1）实盘账户快照（REAL / US / MARGIN）")
    lines.append(f"- 账户快照时间：{_dt_local(acct.get('timestamp'))}")
    try:
        acct_ts = str(acct.get("timestamp") or "")
        if acct_ts[:10] and acct_ts[:10] != today_stamp():
            lines.append(f"- 注意：当前读取到的实盘快照非今日生成（快照日期 {acct_ts[:10]}），可能为缓存；请优先以 moomoo 客户端显示为准。")
    except Exception:
        pass
    lines.append(f"- 账户：{acct.get('acc_id_masked') or 'N/A'}")
    lines.append(f"- 总资产：{_fmt_num(acct.get('total_assets'))} {acct.get('currency')}")
    lines.append(f"- 现金：{_fmt_num(acct.get('cash'))} {acct.get('currency')}（可提：{_fmt_num(acct.get('cash'))}）")
    lines.append(f"- 购买力：{_fmt_num(acct.get('buying_power'))} {acct.get('currency')}")
    lines.append(f"- 风险状态：{acct.get('risk_status')}")
    lines.append(f"- 保证金：初始 {_fmt_num(acct.get('initial_margin'))} / 维持 {_fmt_num(acct.get('maintenance_margin'))} / 追保线 {_fmt_num(acct.get('margin_call_margin'))}")
    lines.append(f"- 美元现金：{_fmt_num(acct.get('us_cash'))} USD；美元资产：{_fmt_num(acct.get('usd_assets'))} USD")
    lines.append(f"- 未成交订单：{acct.get('open_orders_count')}（总订单记录 {acct.get('orders_count_total')}）")
    if acct.get("is_pdt"):
        lines.append(f"- PDT：是（序列 {acct.get('pdt_seq') or 'N/A'}）")
    else:
        lines.append(f"- PDT：否（序列 {acct.get('pdt_seq') or 'N/A'}）")
    lines.append("")

    lines.append("2）持仓盈亏与风险暴露")
    lines.append(f"- 持仓数：{positions.get('count')}；美股持仓市值合计：{_fmt_num(positions.get('total_market_val_usd'))} USD")
    top_positions = positions.get("top") or []
    for pos in top_positions[:8]:
        code = pos.get("code")
        name = (pos.get("stock_name") or "").strip() or code
        qty = pos.get("qty")
        mv = pos.get("market_val")
        pl = pos.get("pl_val")
        pl_ratio = pos.get("pl_ratio")
        today_pl = pos.get("today_pl_val")
        w = pos.get("_weight")
        lines.append(
            f"- {code} {name}：{_fmt_num(qty,0)} 股，市值 {_fmt_num(mv)} USD，浮盈亏 {_fmt_num(pl)} USD（{_fmt_pct(pl_ratio)}），当日 {_fmt_num(today_pl)} USD；仓位占比≈{_fmt_num(w)}%"
        )
    lines.append("")

    lines.append("3）最近一个美股交易日复盘（以市场快照为准）")
    lines.append(f"- 市场快照时间：{_dt_local(market.get('timestamp'))}")
    idx_lines = []
    for idx in (market.get("indices") or [])[:4]:
        idx_lines.append(f"{idx.get('symbol')} {idx.get('label')}: {_fmt_num(idx.get('last'))}（{_fmt_pct(idx.get('dayChangePct'))}）")
    if idx_lines:
        lines.append("- 指数：" + "；".join(idx_lines))
    vix = market.get("vix") or {}
    us10y = market.get("us10y") or {}
    usdcnh = market.get("usdcnh") or {}
    lines.append(f"- VIX：{_fmt_num(vix.get('last'))}（{_fmt_pct(vix.get('dayChangePct'))}）")
    lines.append(f"- 10Y：{_fmt_num(us10y.get('last'),3)}%（{_fmt_pct(us10y.get('dayChangePct'))}）")
    lines.append(f"- 美元/离岸人民币：{_fmt_num(usdcnh.get('last'),3)}（日变动 {usdcnh.get('dayChangePct') if usdcnh.get('dayChangePct') is not None else 'N/A'}）")
    lines.append("")

    lines.append("4）今日主要市场/宏观/新闻变量（来自情绪与信息源汇总）")
    mood = (sentiment.get("marketMood") or {}).get("labelZh") or (sentiment.get("marketMood") or {}).get("label") or "N/A"
    crowding = sentiment.get("crowding") or "N/A"
    lines.append(f"- 市场情绪：{mood}；拥挤度：{crowding}")
    for h in (sentiment.get("highlights") or [])[:5]:
        symbols = h.get("symbols") or []
        sym = f"（{','.join(symbols)}）" if symbols else ""
        lines.append(f"- {h.get('title')}{sym}")
    intel_items = intel.get("highlights") or []
    if intel_items:
        lines.append("- 信息雷达（新闻/事件 Top）：")
        for it in intel_items[:5]:
            symbols = it.get("symbols") or []
            sym = f"（{','.join(symbols)}）" if symbols else ""
            lines.append(f"  - {it.get('title')}{sym}")
            lines.append(f"    {it.get('url')}")
    lines.append("")

    lines.append("5）今日可执行建议（仅建议；不自动交易）")
    if not suggestions:
        lines.append("- 无：当前无足够数据生成可执行建议。")
    for s in suggestions:
        lines.append(f"- {s['code']} {s['name']}：建议【{s['action']}】")
        lines.append(f"  - 理由Top3：{'; '.join(s.get('reasons_top3') or [])}")
        lines.append(f"  - 触发价/条件：{s.get('trigger') or 'N/A'}")
        lines.append(f"  - 止损/失效条件：{s.get('invalidation') or 'N/A'}")
        lines.append(f"  - 建议仓位金额：{s.get('sizing_hkd') or 'N/A'}")
    lines.append("")

    lines.append("6）风险提醒（重点看保证金/集中度/频繁交易）")
    lines.append("- 本简报为只读复盘与建议，不会解锁交易、不下单/撤单/改单。")
    lines.append("- 保证金账户请优先关注：风险状态、维持保证金与追保线距离、以及单一高波动标的集中度。")
    lines.append("- 若接近PDT或近期交易频率升高，优先降低换手、避免当日多次来回。")
    if open_orders:
        lines.append(f"- 当前检测到未完成订单 {len(open_orders)} 条：请在 moomoo 客户端核对。")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate WeChat-friendly Chinese daily brief for moomoo REAL account.")
    parser.add_argument("--account", default=str(ROOT / "data" / "moomoo_real" / "latest.json"))
    parser.add_argument("--market", default=str(ROOT / "data" / "market" / "latest.json"))
    parser.add_argument("--social", default=str(ROOT / "data" / "social_sentiment" / "latest.json"))
    parser.add_argument("--watchlist", default=str(ROOT / "watchlist.json"))
    parser.add_argument("--intel", default="", help="Optional intelligence snapshot json. Default: auto-pick latest data/intelligence/*_intel.json")
    parser.add_argument("--out", default=str(ROOT / "reports" / f"{today_stamp()}_wechat_moomoo_daily_brief.txt"))
    parser.add_argument("--json-out", default=str(ROOT / "data" / "briefs" / "latest_wechat_moomoo_daily_brief.json"))
    args = parser.parse_args(argv)

    intel_path: Path | None = None
    if str(args.intel or "").strip():
        intel_path = Path(str(args.intel)).expanduser()
        if not intel_path.exists():
            intel_path = None
    if intel_path is None:
        intel_path = _pick_latest_intel_snapshot()

    paths = BriefPaths(
        account_snapshot=Path(args.account),
        market_snapshot=Path(args.market),
        social_snapshot=Path(args.social),
        watchlist=Path(args.watchlist),
        intel_snapshot=intel_path,
    )

    data = build_brief(paths)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = render_wechat_text(data)
    # Windows PowerShell may mis-detect UTF-8 without BOM; use utf-8-sig for WeChat push tooling compatibility.
    out_path.write_text(text, encoding="utf-8-sig")
    write_json(Path(args.json_out), data)
    print(json.dumps({"status": "PASS", "timestamp": data["timestamp"], "out": str(out_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
