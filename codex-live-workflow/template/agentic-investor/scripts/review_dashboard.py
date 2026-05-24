from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from agentic_investor_common import REPORTS_DIR, ROOT, read_json, today_stamp


def _safe(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _load(path: Path, default: Any) -> Any:
    return read_json(path, default)


def _latest_json(folder: Path, pattern: str) -> dict[str, Any]:
    files = sorted(folder.glob(pattern))
    if not files:
        return {}
    return read_json(files[-1], {})


def _records_by_task(log: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in log.get("records", []):
        grouped.setdefault(record.get("task", "unknown"), []).append(record)
    return grouped


def build_dashboard(period: str) -> tuple[Path, str]:
    state = _load(ROOT / "state.json", {})
    rules = _load(ROOT / "rule-engine.json", {})
    watchlist = _load(ROOT / "watchlist.json", {})
    macro = _load(ROOT / "macro-regime.json", {})
    staged = _load(ROOT / "data" / "trading" / "staged-orders.json", {"orders": []})
    trade_log = _load(ROOT / "trade-log.json", {"records": []})
    intel = _latest_json(ROOT / "data" / "intelligence", "*_intel.json")
    earnings = _load(ROOT / "data" / "events" / "earnings_latest.json", {})
    congress = _load(ROOT / "data" / "congress_trades" / "latest.json", {})
    health = _load(ROOT / "data" / "health" / "latest.json", {})
    committee = _load(ROOT / "data" / "research_committee" / "latest.json", {})
    grouped = _records_by_task(trade_log)

    records = trade_log.get("records", [])[-12:]
    watch_items = watchlist.get("watchlist", [])
    excluded = watchlist.get("excluded", [])
    orders = staged.get("orders", [])
    highlights = intel.get("highlights", [])
    earnings_events = earnings.get("events", [])
    congress_signals = congress.get("signals", [])
    macro_current = macro.get("current", {})

    path = REPORTS_DIR / f"{today_stamp()}_{period}_review_dashboard.html"
    data = {
        "state": state,
        "macro": macro_current,
        "macroOverlay": state.get("macro_regime"),
        "watchCount": len(watch_items),
        "excludedCount": len(excluded),
        "orderCount": len(orders),
        "intelCount": len(highlights),
        "earningsCount": len(earnings_events),
        "congressSignalCount": len(congress_signals),
        "healthStatus": health.get("status"),
        "committeeDecision": committee.get("decision"),
        "tasks": {key: len(value) for key, value in grouped.items()},
    }
    task_rows = "".join(
        f"<tr><td>{_safe(record.get('timestamp'))}</td><td>{_safe(record.get('task'))}</td><td>{_safe(record.get('status'))}</td><td>{_safe(record.get('summary'))}</td></tr>"
        for record in records
    )
    watch_rows = "".join(
        f"<tr><td>{_safe(item.get('normalized_symbol') or item.get('symbol'))}</td><td>{_safe(item.get('status'))}</td><td>{_safe(item.get('source'))}</td><td>{_safe(item.get('thesis'))}</td></tr>"
        for item in watch_items
    )
    order_rows = "".join(
        f"<tr><td>{_safe(order.get('intent_id'))}</td><td>{_safe(order.get('status'))}</td><td>{_safe(order.get('intent', {}).get('symbol'))}</td><td>{_safe(order.get('intent', {}).get('side'))}</td><td>{_safe(order.get('guard_result', {}).get('status'))}</td></tr>"
        for order in orders
    )
    intel_rows = "".join(
        f"<tr><td>{_safe(item.get('score'))}</td><td>{_safe(item.get('title'))}</td><td>{_safe(', '.join(item.get('symbol_hits', [])))}</td><td><a href=\"{_safe(item.get('url'))}\">link</a></td></tr>"
        for item in highlights
    )
    earnings_rows = "".join(
        f"<tr><td>{_safe(item.get('symbol'))}</td><td>{_safe(item.get('earnings_date'))}</td><td>{_safe(item.get('days_until'))}</td><td>{_safe(item.get('risk_level'))}</td><td>{_safe(item.get('option_playbook', {}).get('call_action'))}</td></tr>"
        for item in earnings_events[:10]
    )
    congress_rows = "".join(
        f"<tr><td>{_safe(item.get('symbol'))}</td><td>{_safe(round(float(item.get('net_score', 0)), 3))}</td><td>{_safe(item.get('buy_count'))}</td><td>{_safe(item.get('sell_count'))}</td><td>{_safe(', '.join(item.get('members', [])))}</td></tr>"
        for item in congress_signals[:10]
    )

    html_doc = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Agentic Investor {html.escape(period.title())} Review</title>
  <style>
    :root {{
      --ink: #111827;
      --paper: #f7f2e8;
      --sheet: #fffaf0;
      --line: #d7c7aa;
      --muted: #6d6252;
      --accent: #0f766e;
      --accent-2: #b45309;
      --warn: #9a3412;
      --tab-1: #91c7b1;
      --tab-2: #e7b7a5;
      --tab-3: #f0cf75;
      --tab-4: #9fb7d5;
      --slide-padding: clamp(16px, 5vh, 48px);
      --title-size: clamp(32px, 8vh, 72px);
      --h2-size: clamp(24px, 5vh, 46px);
      --body-size: clamp(15px, 2.4vh, 20px);
      --small-size: clamp(12px, 1.8vh, 15px);
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ height: 100%; overflow: hidden; }}
    body {{ margin: 0; font-family: Georgia, 'Times New Roman', serif; color: var(--ink); background: #27231f; }}
    h1, h2 {{ margin: 0; letter-spacing: 0; }}
    h1 {{ font-size: var(--title-size); max-width: 980px; line-height: 0.98; }}
    h2 {{ font-size: var(--h2-size); margin-bottom: clamp(10px, 2vh, 20px); line-height: 1.02; }}
    p {{ font-size: var(--body-size); line-height: 1.45; }}
    .deck {{ width: 100vw; height: 100vh; height: 100dvh; overflow: hidden; position: relative; }}
    .slides {{ height: 100%; transition: transform 500ms cubic-bezier(.22,.61,.36,1); }}
    .slide {{
      width: 100vw;
      height: 100vh;
      height: 100dvh;
      overflow: hidden;
      padding: var(--slide-padding);
      position: relative;
      display: grid;
      align-content: center;
      gap: clamp(12px, 2.5vh, 24px);
      background:
        linear-gradient(90deg, transparent calc(100% - 34px), var(--tab-color, var(--tab-1)) calc(100% - 34px)),
        radial-gradient(circle at 12% 18%, rgba(15, 118, 110, .16), transparent 28%),
        linear-gradient(135deg, var(--paper), var(--sheet));
    }}
    .slide::before {{
      content: '';
      position: absolute;
      left: clamp(14px, 2vw, 28px);
      top: clamp(18px, 3vh, 36px);
      bottom: clamp(18px, 3vh, 36px);
      width: 2px;
      background: repeating-linear-gradient(to bottom, transparent 0 12px, rgba(17,24,39,.22) 12px 18px);
    }}
    .slide-kicker {{ font: 700 var(--small-size)/1.2 'Trebuchet MS', sans-serif; color: var(--accent-2); text-transform: uppercase; }}
    .slide-number {{ position: absolute; right: 46px; top: 18px; font: 700 var(--small-size)/1 'Trebuchet MS', sans-serif; color: rgba(17,24,39,.55); }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: clamp(8px, 2vh, 14px); }}
    .metric, .panel {{ background: rgba(255, 250, 240, .82); border: 1px solid var(--line); border-radius: 8px; padding: clamp(10px, 2vh, 16px); }}
    .metric .label {{ color: var(--muted); font-size: 12px; }}
    .metric .value {{ font-size: clamp(20px, 4vh, 34px); font-weight: 700; margin-top: 8px; }}
    .panel {{ max-height: min(64vh, 620px); overflow: hidden; }}
    table {{ width: 100%; border-collapse: collapse; font: 13px/1.35 'Trebuchet MS', sans-serif; }}
    th, td {{ text-align: left; border-bottom: 1px solid var(--line); padding: 8px 7px; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    .status {{ display: inline-block; padding: 4px 8px; border-radius: 6px; background: #eef6f5; color: var(--accent); font-weight: 650; }}
    .warn {{ color: var(--warn); }}
    .muted {{ color: var(--muted); }}
    .bars {{ display: grid; gap: 8px; }}
    .bar {{ display: grid; grid-template-columns: minmax(110px, 160px) 1fr 40px; gap: 10px; align-items: center; font: 13px/1.2 'Trebuchet MS', sans-serif; }}
    .bar span:nth-child(2) {{ display: block; height: 10px; background: #dbeafe; border-radius: 99px; overflow: hidden; }}
    .bar span:nth-child(2)::before {{ content: ''; display: block; height: 100%; width: var(--w); background: var(--accent); }}
    .progress {{ position: fixed; left: 0; right: 0; bottom: 0; height: 5px; background: rgba(255,255,255,.28); z-index: 8; }}
    .progress-bar {{ height: 100%; width: 0; background: var(--accent); transition: width 300ms ease; }}
    .nav {{ position: fixed; right: 16px; bottom: 18px; display: flex; gap: 6px; z-index: 9; }}
    .dot {{ width: 10px; height: 10px; border-radius: 50%; border: 1px solid rgba(17,24,39,.45); background: rgba(255,255,255,.55); cursor: pointer; }}
    .dot.active {{ background: var(--accent); border-color: var(--accent); }}
    .hint {{ position: fixed; left: 16px; bottom: 16px; font: 12px/1.2 'Trebuchet MS', sans-serif; color: rgba(255,255,255,.72); z-index: 9; }}
    @media (max-height: 700px) {{ :root {{ --slide-padding: 14px; --title-size: 38px; --h2-size: 28px; --body-size: 15px; }} .panel {{ max-height: 58vh; }} }}
    @media (max-height: 600px) {{ .decorative, .hint {{ display: none; }} .metric .value {{ font-size: 22px; }} table {{ font-size: 11px; }} th, td {{ padding: 5px; }} }}
    @media (max-width: 900px) {{ .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
    @media (max-width: 560px) {{ .grid {{ grid-template-columns: 1fr; }} .slide::before {{ display: none; }} }}
    @media (prefers-reduced-motion: reduce) {{ .slides {{ transition: none; }} *, *::before, *::after {{ animation-duration: .01ms !important; transition-duration: .01ms !important; }} }}
  </style>
</head>
<body>
  <main class=\"deck\">
    <div class=\"slides\" id=\"slides\">
    <section class=\"slide\" data-title=\"Overview\" style=\"--tab-color: var(--tab-1)\">
      <span class=\"slide-kicker\">Agentic Investor</span>
      <span class=\"slide-number\">01 / 06</span>
      <h1>{html.escape(period.title())} Review</h1>
      <div class=\"grid\">
        <div class=\"metric\"><div class=\"label\">Mode</div><div class=\"value\">{_safe(state.get('mode'))}</div></div>
        <div class=\"metric\"><div class=\"label\">Execution</div><div class=\"value\">{_safe(state.get('execution_mode'))}</div></div>
        <div class=\"metric\"><div class=\"label\">Market</div><div class=\"value\">{_safe(state.get('market_state'))}</div></div>
        <div class=\"metric\"><div class=\"label\">Macro</div><div class=\"value warn\">{_safe(state.get('macro_regime'))}</div></div>
      </div>
      <div class=\"grid\">
        <div class=\"metric\"><div class=\"label\">Earnings events</div><div class=\"value\">{len(earnings_events)}</div></div>
        <div class=\"metric\"><div class=\"label\">Option blackout</div><div class=\"value warn\">{len(earnings.get('blocked_option_symbols', []))}</div></div>
        <div class=\"metric\"><div class=\"label\">Congress signals</div><div class=\"value\">{len(congress_signals)}</div></div>
        <div class=\"metric\"><div class=\"label\">Staged intents</div><div class=\"value\">{len(orders)}</div></div>
        <div class=\"metric\"><div class=\"label\">Health</div><div class=\"value warn\">{_safe(health.get('status', 'n/a'))}</div></div>
        <div class=\"metric\"><div class=\"label\">Committee</div><div class=\"value\">{_safe(committee.get('decision', 'n/a'))}</div></div>
      </div>
      <div class=\"panel\">
        <h2>Macro Gate</h2>
        <p><span class=\"status\">Fed target: {_safe(macro_current.get('fed_funds_target_range', 'unknown'))}</span></p>
        <p class=\"muted\">{_safe(macro_current.get('notes', ''))}</p>
      </div>
      <div class=\"panel\">
        <h2>Activity</h2>
        <div class=\"bars\" id=\"taskBars\"></div>
      </div>
    </section>
    <section class=\"slide\" data-title=\"Intel\" style=\"--tab-color: var(--tab-2)\">
      <span class=\"slide-kicker\">Information Radar</span>
      <span class=\"slide-number\">02 / 06</span>
      <h2>Intelligence Highlights</h2>
      <div class=\"panel\"><table><thead><tr><th>Score</th><th>Title</th><th>Symbols</th><th>URL</th></tr></thead><tbody>{intel_rows or '<tr><td colspan=\"4\">No intelligence highlights yet.</td></tr>'}</tbody></table></div>
    </section>
    <section class=\"slide\" data-title=\"Events\" style=\"--tab-color: var(--tab-2)\">
      <span class=\"slide-kicker\">Daily Event Risk</span>
      <span class=\"slide-number\">03 / 06</span>
      <h2>Earnings And Option Gates</h2>
      <div class=\"panel\"><table><thead><tr><th>Symbol</th><th>Date</th><th>Days</th><th>Risk</th><th>Call Guidance</th></tr></thead><tbody>{earnings_rows or '<tr><td colspan=\"5\">No watched earnings events in the current window.</td></tr>'}</tbody></table></div>
    </section>
    <section class=\"slide\" data-title=\"Watchlist\" style=\"--tab-color: var(--tab-3)\">
      <span class=\"slide-kicker\">Human-in-the-loop</span>
      <span class=\"slide-number\">04 / 06</span>
      <h2>Watchlist And Exclusions</h2>
      <div class=\"grid\">
        <div class=\"metric\"><div class=\"label\">Watchlist</div><div class=\"value\">{len(watch_items)}</div></div>
        <div class=\"metric\"><div class=\"label\">Excluded</div><div class=\"value\">{len(excluded)}</div></div>
        <div class=\"metric\"><div class=\"label\">Last Review</div><div class=\"value\">{_safe(state.get('last_watchlist_review', 'n/a'))}</div></div>
        <div class=\"metric\"><div class=\"label\">Learning</div><div class=\"value\">{_safe(state.get('learning_level', 'n/a'))}</div></div>
      </div>
      <div class=\"panel\"><table><thead><tr><th>Symbol</th><th>Status</th><th>Source</th><th>Thesis</th></tr></thead><tbody>{watch_rows or '<tr><td colspan=\"4\">No watchlist entries.</td></tr>'}</tbody></table></div>
    </section>
    <section class=\"slide\" data-title=\"Intents\" style=\"--tab-color: var(--tab-4)\">
      <span class=\"slide-kicker\">Trading-as-Git</span>
      <span class=\"slide-number\">05 / 06</span>
      <h2>Trading-as-Git Intents</h2>
      <div class=\"panel\"><table><thead><tr><th>Intent</th><th>Status</th><th>Symbol</th><th>Side</th><th>Guard</th></tr></thead><tbody>{order_rows or '<tr><td colspan=\"5\">No staged intents.</td></tr>'}</tbody></table></div>
      <h2>Congressional Signal Watch</h2>
      <div class=\"panel\"><table><thead><tr><th>Symbol</th><th>Net Score</th><th>Buys</th><th>Sells</th><th>Members</th></tr></thead><tbody>{congress_rows or '<tr><td colspan=\"5\">No congressional signals yet.</td></tr>'}</tbody></table></div>
    </section>
    <section class=\"slide\" data-title=\"Log\" style=\"--tab-color: var(--tab-1)\">
      <span class=\"slide-kicker\">Audit Trail</span>
      <span class=\"slide-number\">06 / 06</span>
      <h2>Recent Audit Log</h2>
      <div class=\"panel\"><table><thead><tr><th>Time</th><th>Task</th><th>Status</th><th>Summary</th></tr></thead><tbody>{task_rows or '<tr><td colspan=\"4\">No audit log records.</td></tr>'}</tbody></table></div>
    </section>
    </div>
    <div class=\"nav\" id=\"navDots\" aria-label=\"Slide navigation\"></div>
    <div class=\"progress\" aria-hidden=\"true\"><div class=\"progress-bar\" id=\"progressBar\"></div></div>
    <div class=\"hint\">← → / Space</div>
  </main>
  <script>
    const data = {json.dumps(data, ensure_ascii=False)};
    class SlideDeck {{
      constructor() {{
        this.track = document.getElementById('slides');
        this.slides = [...document.querySelectorAll('.slide')];
        this.progress = document.getElementById('progressBar');
        this.nav = document.getElementById('navDots');
        this.index = 0;
        this.touchStart = null;
        this.buildDots();
        this.bind();
        this.go(0);
      }}
      buildDots() {{
        this.nav.innerHTML = this.slides.map((slide, i) => `<button class=\"dot\" aria-label=\"${{slide.dataset.title}}\" data-index=\"${{i}}\"></button>`).join('');
        this.nav.querySelectorAll('button').forEach(dot => dot.addEventListener('click', () => this.go(Number(dot.dataset.index))));
      }}
      bind() {{
        document.addEventListener('keydown', event => {{
          if (['ArrowRight', 'PageDown', ' '].includes(event.key)) this.next();
          if (['ArrowLeft', 'PageUp'].includes(event.key)) this.prev();
        }});
        document.addEventListener('wheel', event => {{
          if (Math.abs(event.deltaY) < 30) return;
          event.deltaY > 0 ? this.next() : this.prev();
        }}, {{ passive: true }});
        document.addEventListener('touchstart', event => {{ this.touchStart = event.touches[0].clientX; }}, {{ passive: true }});
        document.addEventListener('touchend', event => {{
          if (this.touchStart === null) return;
          const delta = event.changedTouches[0].clientX - this.touchStart;
          if (Math.abs(delta) > 40) delta < 0 ? this.next() : this.prev();
          this.touchStart = null;
        }}, {{ passive: true }});
      }}
      go(index) {{
        this.index = Math.max(0, Math.min(index, this.slides.length - 1));
        this.track.style.transform = `translateY(${{-100 * this.index}}vh)`;
        this.progress.style.width = `${{((this.index + 1) / this.slides.length) * 100}}%`;
        this.nav.querySelectorAll('.dot').forEach((dot, i) => dot.classList.toggle('active', i === this.index));
      }}
      next() {{ this.go(this.index + 1); }}
      prev() {{ this.go(this.index - 1); }}
    }}
    new SlideDeck();
    const tasks = Object.entries(data.tasks).sort((a, b) => b[1] - a[1]);
    const max = Math.max(1, ...tasks.map(item => item[1]));
    document.getElementById('taskBars').innerHTML = tasks.map(([name, count]) => {{
      const width = Math.max(6, Math.round(count / max * 100));
      return `<div class=\"bar\"><strong>${{name}}</strong><span style=\"--w:${{width}}%\"></span><em>${{count}}</em></div>`;
    }}).join('') || '<p class=\"muted\">No task activity yet.</p>';
  </script>
</body>
</html>"""
    path.write_text(html_doc, encoding="utf-8")
    return path, str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an interactive static HTML review dashboard.")
    parser.add_argument("--period", choices=["daily", "weekly", "quarterly"], default="weekly")
    args = parser.parse_args()
    path, _ = build_dashboard(args.period)
    print(json.dumps({"status": "completed", "html": str(path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
