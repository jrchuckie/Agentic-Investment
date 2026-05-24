from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from agentic_investor_common import REPORTS_DIR, now_iso, today_stamp, write_json
from moomoo_data import fetch_research_snapshot


ROOT = Path(__file__).resolve().parents[1]
TRACKER_PATH = ROOT / "data" / "fund_holdings" / "latest.json"
BACKTEST_PATH = ROOT / "reports" / f"{today_stamp()}_backtest_v2.json"


THEMES = {
    "AI Compute & Semis": {
        "tickers": {
            "NVDA", "AVGO", "TSM", "AMD", "MU", "MRVL", "LRCX", "WDC", "NXPI",
            "MPWR", "ON", "ASML", "TSEM", "ALAB", "ARM", "SMH", "ANET", "DELL",
            "SMCI", "TER",
        },
        "pitch": "Still the cleanest read-through from manager disclosures: AI training/inference, custom silicon, memory, networking, and wafer-capex are where the disclosed weights cluster.",
        "risk": "Crowding and valuation. Treat NVDA/AVGO as core quality, and use memory/storage names as cyclical satellites rather than permanent compounders.",
    },
    "Hyperscale AI Platforms": {
        "tickers": {"MSFT", "AMZN", "GOOG", "GOOGL", "META", "AAPL", "ORCL", "NFLX"},
        "pitch": "The mega-cap platform layer is the lower-drama way to own AI monetization: distribution, cloud budgets, ads, app ecosystems, and model integration.",
        "risk": "The trade can lag semis when risk appetite is hot; regulatory headlines and capex discipline matter.",
    },
    "AI Software & Data Apps": {
        "tickers": {
            "APP", "PLTR", "GTLB", "DDOG", "SNOW", "SHOP", "RDDT", "BILL", "CRM",
            "NOW", "ROKU", "UBER", "TEM",
        },
        "pitch": "This is the higher-beta alpha hunting zone: application software, data infrastructure, developer tools, ad-tech, and vertical AI.",
        "risk": "Revenue acceleration must show up. Use tighter sizing and require price trend confirmation.",
    },
    "Fintech & Crypto Rails": {
        "tickers": {"COIN", "CRCL", "HOOD", "MELI", "PYPL", "SQ"},
        "pitch": "Cathie-style innovation exposure shows up here: tokenization, exchange volume, stablecoin rails, and retail trading engagement.",
        "risk": "Policy and liquidity are the trade. These are tactical ideas, not portfolio ballast.",
    },
    "Autonomy / Robotics / Bio-AI": {
        "tickers": {"TSLA", "CRSP", "KTOS", "ISRG", "PATH"},
        "pitch": "A smaller, more optionality-heavy basket where managers are paying for non-linear upside rather than near-term earnings certainty.",
        "risk": "Narrative duration risk is high. Size it like optionality.",
    },
}


IDEA_NOTES = {
    "NVDA": ("Core AI compute", "Highest cross-manager consensus in the feed; keep as core exposure, but avoid adding aggressively after vertical moves."),
    "AVGO": ("Custom silicon / networking", "Best complement to NVDA: AI ASICs, networking, and software cash flow give it both growth and quality."),
    "TSM": ("Foundry gatekeeper", "Broad consensus plus supply-chain scarcity. Main risk is geopolitics and Taiwan concentration."),
    "MSFT": ("Enterprise AI distribution", "Owns enterprise workflow, Azure, and model distribution; cleaner quality compounder than many high-beta AI apps."),
    "AMZN": ("AWS + retail operating leverage", "Good platform idea when managers want AI cloud but not pure semiconductor cyclicality."),
    "GOOGL": ("AI search reset", "Manager-owned but still debated; attractive when market over-discounts search disruption."),
    "AMD": ("Second-source AI compute", "Good upside torque if accelerator share gains continue; more execution risk than NVDA/AVGO."),
    "MU": ("AI memory cycle", "Memory is moving from commodity beta to AI bottleneck. Keep it cyclical, not sacred."),
    "MRVL": ("AI networking ASICs", "Backtest is currently picking it; good satellite if trend remains above key averages."),
    "WDC": ("Storage / NAND recovery", "Momentum-backed and appears in manager feed. High beta to storage cycle, so position sizing matters."),
    "LRCX": ("Wafer equipment", "A picks-and-shovels way to own semiconductor capex without betting on one GPU winner."),
    "AAPL": ("On-device AI optionality", "Less pure AI, but still a major platform holding. Better as stabilizer than alpha spear."),
    "META": ("AI ads + compute discipline", "Strong platform economics; key question is whether AI capex keeps translating into ad yield."),
    "APP": ("AI ad-tech", "High-beta software idea with real manager sponsorship; works best in risk-on tape."),
    "RDDT": ("Data + ads", "Emerging AI/data licensing and ad platform angle; volatility likely stays high."),
    "SHOP": ("Commerce operating system", "ARK-style innovation idea with better business quality than many speculative names."),
    "TSLA": ("Autonomy option", "Manager-backed but thesis is narrative-heavy; separate core EV fundamentals from autonomy optionality."),
    "TEM": ("Vertical AI health data", "Appears in innovation funds; treat as venture-like public equity exposure."),
}


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_num(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def _theme_for(symbol: str) -> str:
    for theme, meta in THEMES.items():
        if symbol in meta["tickers"]:
            return theme
    return "Other Tech / Growth"


def _source_dates(holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_source: dict[str, dict[str, Any]] = {}
    for holding in holdings:
        source_id = holding.get("source_id", "")
        row = by_source.setdefault(
            source_id,
            {
                "source_id": source_id,
                "manager": holding.get("manager", ""),
                "vehicle": holding.get("vehicle", ""),
                "source_type": holding.get("source_type", ""),
                "as_of": set(),
                "filing_date": set(),
                "count": 0,
            },
        )
        row["count"] += 1
        if holding.get("as_of"):
            row["as_of"].add(str(holding["as_of"]))
        if holding.get("filing_date"):
            row["filing_date"].add(str(holding["filing_date"]))
    rows = []
    for row in by_source.values():
        rows.append(
            {
                **row,
                "as_of": ", ".join(sorted(row["as_of"])) or "n/a",
                "filing_date": ", ".join(sorted(row["filing_date"])) or "n/a",
            }
        )
    return sorted(rows, key=lambda item: item["source_id"])


def _theme_rows(aggregate: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_theme: dict[str, dict[str, Any]] = defaultdict(lambda: {"score": 0.0, "symbols": [], "manager_votes": 0})
    for row in aggregate:
        symbol = row.get("symbol", "")
        theme = _theme_for(symbol)
        by_theme[theme]["score"] += float(row.get("score", 0.0))
        by_theme[theme]["manager_votes"] += int(row.get("manager_count", 0))
        by_theme[theme]["symbols"].append(symbol)
    rows = []
    total = sum(item["score"] for item in by_theme.values()) or 1.0
    for theme, item in by_theme.items():
        rows.append(
            {
                "theme": theme,
                "score": item["score"],
                "share": item["score"] / total,
                "manager_votes": item["manager_votes"],
                "symbols": item["symbols"][:8],
                "pitch": THEMES.get(theme, {}).get("pitch", "Manager-backed growth exposure with thinner signal quality than the primary themes."),
                "risk": THEMES.get(theme, {}).get("risk", "Use smaller sizing until fresh disclosures and price trend confirm the thesis."),
            }
        )
    return sorted(rows, key=lambda item: item["score"], reverse=True)


def _fetch_momentum(symbols: list[str]) -> dict[str, dict[str, Any]]:
    try:
        research = fetch_research_snapshot(symbols, lookback_days=420)
    except Exception:
        return {}
    return {row.get("symbol"): row for row in research.get("records", [])}


def _idea_score(row: dict[str, Any], momentum: dict[str, Any]) -> float:
    manager_score = float(row.get("score", 0.0))
    consensus = min(int(row.get("manager_count", 0)) / 6.0, 1.0)
    mom_30 = momentum.get("momentum_30d_pct")
    trend = 0.0
    if momentum.get("above_ma50"):
        trend += 0.10
    if momentum.get("above_ma200"):
        trend += 0.10
    try:
        mom_part = max(min(float(mom_30) / 30.0, 0.25), -0.15)
    except (TypeError, ValueError):
        mom_part = 0.0
    return manager_score + 0.08 * consensus + trend + mom_part


def _idea_rows(aggregate: list[dict[str, Any]], momentum_by_symbol: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for row in aggregate[:45]:
        symbol = row["symbol"]
        label, note = IDEA_NOTES.get(
            symbol,
            (_theme_for(symbol), "Manager-backed name. Promote only if price trend and portfolio exposure leave room."),
        )
        momentum = momentum_by_symbol.get(symbol, {})
        candidates.append(
            {
                **row,
                "theme": _theme_for(symbol),
                "label": label,
                "note": note,
                "momentum": momentum,
                "idea_score": _idea_score(row, momentum),
            }
        )
    candidates.sort(key=lambda item: item["idea_score"], reverse=True)
    return candidates[:18]


def _html_escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _bar(value: float, max_value: float, color: str = "teal") -> str:
    width = 0 if max_value <= 0 else max(4, min(100, value / max_value * 100))
    return f'<div class="bar"><span class="{color}" style="width:{width:.1f}%"></span></div>'


def _chip(text: str, tone: str = "") -> str:
    return f'<span class="chip {tone}">{_html_escape(text)}</span>'


def build_html(tracker: dict[str, Any], backtest: dict[str, Any], momentum_by_symbol: dict[str, dict[str, Any]]) -> str:
    aggregate = tracker.get("aggregate", [])
    holdings = tracker.get("holdings", [])
    feed = tracker.get("backtest_feed", {})
    source_rows = _source_dates(holdings)
    themes = _theme_rows(aggregate)
    ideas = _idea_rows(aggregate, momentum_by_symbol)
    max_theme_score = max((row["score"] for row in themes), default=1.0)
    max_idea_score = max((row["score"] for row in ideas), default=1.0)
    backtest_variants = backtest.get("variants", [])
    best = backtest_variants[0] if backtest_variants else {}
    latest_picks = best.get("latest_rebalance", {}).get("picks", [])
    generated_at = now_iso()

    theme_cards = []
    for idx, row in enumerate(themes[:5], start=1):
        symbols = " ".join(_chip(symbol) for symbol in row["symbols"][:7])
        theme_cards.append(
            f"""
            <article class="theme-card">
              <div class="theme-rank">0{idx}</div>
              <div>
                <h3>{_html_escape(row['theme'])}</h3>
                <p>{_html_escape(row['pitch'])}</p>
                <div class="theme-meter">{_bar(row['score'], max_theme_score, 'accent')}</div>
                <div class="chips">{symbols}</div>
                <p class="risk">Risk: {_html_escape(row['risk'])}</p>
              </div>
            </article>
            """
        )

    idea_rows_html = []
    for row in ideas:
        symbol = row["symbol"]
        momentum = row.get("momentum", {})
        top_source = row.get("weighted_sources", [{}])[0]
        trend = []
        if momentum.get("above_ma50"):
            trend.append("above MA50")
        if momentum.get("above_ma200"):
            trend.append("above MA200")
        if not trend:
            trend.append("trend unconfirmed")
        idea_rows_html.append(
            f"""
            <tr>
              <td>
                <strong>{_html_escape(symbol)}</strong>
                <span>{_html_escape(row['label'])}</span>
              </td>
              <td>{_html_escape(row['theme'])}</td>
              <td>{_fmt_num(row.get('score'), 3)} {_bar(float(row.get('score', 0.0)), max_idea_score, 'teal')}</td>
              <td>{int(row.get('manager_count', 0))}</td>
              <td>{_fmt_pct(row.get('total_weight_pct'))}</td>
              <td>{_fmt_pct(momentum.get('momentum_30d_pct'))}</td>
              <td>{_html_escape(', '.join(trend))}</td>
              <td>{_html_escape(top_source.get('source_id', 'n/a'))} {_fmt_pct(top_source.get('weight_pct'))}</td>
              <td>{_html_escape(row['note'])}</td>
            </tr>
            """
        )

    source_rows_html = []
    for row in source_rows:
        source_rows_html.append(
            f"""
            <tr>
              <td>{_html_escape(row['source_id'])}</td>
              <td>{_html_escape(row['manager'])}</td>
              <td>{_html_escape(row['vehicle'])}</td>
              <td>{_html_escape(row['source_type'])}</td>
              <td>{_html_escape(row['as_of'])}</td>
              <td>{_html_escape(row['filing_date'])}</td>
              <td>{int(row['count'])}</td>
            </tr>
            """
        )

    top_symbols = " ".join(_chip(symbol, "hot") for symbol in feed.get("candidate_symbols", [])[:12])
    pick_chips = " ".join(_chip(symbol, "pick") for symbol in latest_picks) or _chip("n/a")
    source_success = f"{tracker.get('successful_source_count', 0)}/{tracker.get('source_count', 0)}"
    best_metrics = best.get("metrics", {})

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Manager Holdings Idea Pitch</title>
  <style>
    :root {{
      --ink: #20242a;
      --muted: #64707d;
      --paper: #fbfaf6;
      --panel: #ffffff;
      --line: #dde3ea;
      --teal: #0d9488;
      --blue: #2563eb;
      --amber: #d97706;
      --coral: #e85d4f;
      --green: #4f8a3f;
      --violet: #7c3aed;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--paper);
      color: var(--ink);
      letter-spacing: 0;
    }}
    header {{
      padding: 28px 32px 18px;
      background: #fff;
      border-bottom: 1px solid var(--line);
    }}
    .topline {{
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 14px;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(30px, 4vw, 56px);
      line-height: 0.98;
      letter-spacing: 0;
      max-width: 980px;
    }}
    .subtitle {{
      margin: 14px 0 0;
      max-width: 980px;
      color: #404852;
      font-size: 17px;
      line-height: 1.5;
    }}
    main {{
      max-width: 1380px;
      margin: 0 auto;
      padding: 24px;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 22px;
    }}
    .stat, .panel, .theme-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .stat {{
      padding: 16px;
      min-height: 92px;
    }}
    .stat span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      margin-bottom: 8px;
    }}
    .stat strong {{
      font-size: 28px;
      line-height: 1.1;
    }}
    .grid {{
      display: grid;
      grid-template-columns: 1.05fr 0.95fr;
      gap: 16px;
      align-items: start;
    }}
    .panel {{
      padding: 18px;
      margin-bottom: 16px;
    }}
    .panel h2 {{
      margin: 0 0 12px;
      font-size: 18px;
    }}
    .pitch-list {{
      display: grid;
      gap: 12px;
    }}
    .theme-card {{
      display: grid;
      grid-template-columns: 42px 1fr;
      gap: 14px;
      padding: 16px;
    }}
    .theme-rank {{
      color: var(--coral);
      font-weight: 800;
      font-size: 20px;
    }}
    .theme-card h3 {{
      margin: 0 0 7px;
      font-size: 17px;
    }}
    .theme-card p {{
      margin: 0 0 10px;
      color: #46515d;
      line-height: 1.45;
    }}
    .risk {{
      font-size: 13px;
    }}
    .chips {{
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin: 8px 0;
    }}
    .chip {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 9px;
      font-size: 12px;
      background: #f7f9fb;
      color: #26313d;
      white-space: nowrap;
    }}
    .chip.hot {{ border-color: #f4c166; background: #fff7de; }}
    .chip.pick {{ border-color: #8ed4cb; background: #e9fbf8; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 10px 9px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      background: #f7f8fa;
      position: sticky;
      top: 0;
      z-index: 1;
    }}
    td strong {{
      display: block;
      font-size: 16px;
    }}
    td span {{
      color: var(--muted);
      font-size: 12px;
    }}
    .table-wrap {{
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .bar {{
      width: 100%;
      height: 7px;
      background: #edf1f5;
      border-radius: 999px;
      overflow: hidden;
      margin-top: 6px;
    }}
    .bar span {{
      display: block;
      height: 100%;
    }}
    .bar .teal {{ background: var(--teal); }}
    .bar .accent {{ background: linear-gradient(90deg, var(--teal), var(--blue), var(--amber)); }}
    .note {{
      border-left: 4px solid var(--amber);
      background: #fff8e8;
      padding: 12px 14px;
      line-height: 1.45;
      color: #513b12;
      border-radius: 4px;
    }}
    .calls {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }}
    .callout {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      background: #fbfdff;
    }}
    .callout b {{
      display: block;
      margin-bottom: 6px;
    }}
    footer {{
      color: var(--muted);
      padding: 14px 32px 32px;
      font-size: 12px;
    }}
    @media (max-width: 920px) {{
      main {{ padding: 16px; }}
      header {{ padding: 22px 18px 16px; }}
      .stats, .grid, .calls {{ grid-template-columns: 1fr; }}
      table {{ min-width: 980px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="topline">
      <span>Generated {generated_at}</span>
      <span>Tracker snapshot {tracker.get('timestamp', 'n/a')}</span>
      <span>Advisory-only research</span>
    </div>
    <h1>Manager Holdings Pitch: AI Infrastructure First, Software Selectively</h1>
    <p class="subtitle">The latest public disclosures point to a barbell: own the AI compute supply chain as the primary engine, keep hyperscale platforms as quality ballast, and use software/innovation names only where manager sponsorship and price trend both agree.</p>
  </header>
  <main>
    <section class="stats">
      <div class="stat"><span>Disclosure sources</span><strong>{source_success}</strong></div>
      <div class="stat"><span>Parsed holdings</span><strong>{len(holdings)}</strong></div>
      <div class="stat"><span>Backtest feed</span><strong>{len(feed.get('candidate_symbols', []))}</strong></div>
      <div class="stat"><span>Best v2 CAGR</span><strong>{_fmt_pct(best_metrics.get('cagr_pct'))}</strong></div>
    </section>

    <section class="grid">
      <div>
        <section class="panel">
          <h2>Sector Pitch</h2>
          <div class="pitch-list">
            {''.join(theme_cards)}
          </div>
        </section>
      </div>
      <aside>
        <section class="panel">
          <h2>This Week's Read</h2>
          <div class="calls">
            <div class="callout"><b>Core</b>NVDA, AVGO, TSM, MSFT. Highest sponsorship, best AI read-through.</div>
            <div class="callout"><b>Satellite</b>MRVL, WDC, MU, LRCX. More cyclical, but the model is listening.</div>
            <div class="callout"><b>Optionality</b>APP, RDDT, SHOP, TSLA, TEM. Smaller sizing; require trend support.</div>
          </div>
          <p class="note">Important: 13F data is quarterly and delayed. ETF/fund data is fresher, but this HTML uses the latest available disclosures, not a clean point-in-time historical tape.</p>
        </section>
        <section class="panel">
          <h2>Top Manager Feed</h2>
          <div class="chips">{top_symbols}</div>
        </section>
        <section class="panel">
          <h2>Backtest v2 Latest Picks</h2>
          <div class="chips">{pick_chips}</div>
          <p class="note">If a backtest pick is not also in the manager feed, treat it as momentum-only rather than manager-confirmed.</p>
        </section>
      </aside>
    </section>

    <section class="panel">
      <h2>Individual Ideas</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Ticker</th>
              <th>Theme</th>
              <th>Manager Score</th>
              <th>Managers</th>
              <th>Total Source Weight</th>
              <th>30D Momentum</th>
              <th>Trend</th>
              <th>Top Source</th>
              <th>Pitch</th>
            </tr>
          </thead>
          <tbody>{''.join(idea_rows_html)}</tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <h2>Disclosure Freshness</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Manager</th>
              <th>Vehicle</th>
              <th>Type</th>
              <th>As Of</th>
              <th>Filing Date</th>
              <th>Rows</th>
            </tr>
          </thead>
          <tbody>{''.join(source_rows_html)}</tbody>
        </table>
      </div>
    </section>
  </main>
  <footer>
    Research only. No account was queried for holdings, no broker state was changed, and no order was placed.
  </footer>
</body>
</html>
"""


def write_pitch(args: argparse.Namespace) -> tuple[Path, Path]:
    tracker = _read_json(args.tracker_path)
    if not tracker:
        raise FileNotFoundError(args.tracker_path)
    backtest = _read_json(args.backtest_path, {})
    symbols = tracker.get("backtest_feed", {}).get("candidate_symbols", [])[: args.momentum_symbols]
    momentum_by_symbol = _fetch_momentum(symbols) if args.with_moomoo else {}
    html_text = build_html(tracker, backtest, momentum_by_symbol)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    html_path = REPORTS_DIR / f"{today_stamp()}_manager_idea_pitch.html"
    json_path = REPORTS_DIR / f"{today_stamp()}_manager_idea_pitch.json"
    html_path.write_text(html_text, encoding="utf-8")
    write_json(
        json_path,
        {
            "task": "manager_idea_pitch",
            "timestamp": now_iso(),
            "html": str(html_path.relative_to(ROOT)),
            "tracker": str(Path(args.tracker_path).relative_to(ROOT)),
            "backtest": str(Path(args.backtest_path).relative_to(ROOT)) if Path(args.backtest_path).exists() else None,
            "momentum_symbols": symbols,
            "momentum_available": bool(momentum_by_symbol),
        },
    )
    return html_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an HTML pitch from latest manager holdings and backtest feed.")
    parser.add_argument("--tracker-path", type=Path, default=TRACKER_PATH)
    parser.add_argument("--backtest-path", type=Path, default=BACKTEST_PATH)
    parser.add_argument("--momentum-symbols", type=int, default=30)
    parser.add_argument("--no-moomoo", dest="with_moomoo", action="store_false")
    parser.set_defaults(with_moomoo=True)
    args = parser.parse_args()
    html_path, json_path = write_pitch(args)
    print(json.dumps({"status": "completed", "html": str(html_path), "json": str(json_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
