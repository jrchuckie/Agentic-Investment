from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from agentic_investor_common import REPORTS_DIR, ROOT, read_json, today_stamp


DEFAULT_INCLUDE = REPORTS_DIR / "2026-05-03_backtest_v2.json"
DEFAULT_EXCLUDE = REPORTS_DIR / "2026-05-03_backtest_v2_exclude_sndk.json"


def _pct(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _usd(value: Any) -> str:
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return "n/a"


def _safe(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _variant_rows(result: dict[str, Any], max_rows: int = 5) -> str:
    rows = []
    for variant in result.get("variants", [])[:max_rows]:
        metrics = variant.get("metrics", {})
        latest = variant.get("latest_rebalance", {})
        rows.append(
            "<tr>"
            f"<td>{_safe(variant.get('variant'))}</td>"
            f"<td>{_pct(metrics.get('cagr_pct'))}</td>"
            f"<td>{_pct(metrics.get('total_return_pct'))}</td>"
            f"<td>{_pct(metrics.get('max_drawdown_pct'))}</td>"
            f"<td>{_pct(metrics.get('annual_volatility_pct'))}</td>"
            f"<td>{float(metrics.get('sharpe_no_rf') or 0):.2f}</td>"
            f"<td>{_safe(', '.join(latest.get('picks', [])))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _best(result: dict[str, Any]) -> dict[str, Any]:
    variants = result.get("variants", [])
    return variants[0] if variants else {}


def _curve_points(variant: dict[str, Any], max_points: int = 180) -> list[dict[str, Any]]:
    curve = variant.get("equity_curve", [])
    if len(curve) <= max_points:
        return curve
    step = max(1, len(curve) // max_points)
    sampled = curve[::step]
    if sampled[-1] != curve[-1]:
        sampled.append(curve[-1])
    return sampled


def _top_drawdown_label(variant: dict[str, Any]) -> str:
    metrics = variant.get("metrics", {})
    return _pct(metrics.get("max_drawdown_pct"))


def build_demo(include_path: Path, exclude_path: Path) -> Path:
    include = read_json(include_path)
    exclude = read_json(exclude_path)
    include_best = _best(include)
    exclude_best = _best(exclude)
    include_metrics = include_best.get("metrics", {})
    exclude_metrics = exclude_best.get("metrics", {})

    data = {
        "include": {
            "label": "含 SNDK",
            "bestName": include_best.get("variant"),
            "curve": _curve_points(include_best),
            "metrics": include_metrics,
            "picks": include_best.get("latest_rebalance", {}).get("picks", []),
        },
        "exclude": {
            "label": "排除 SNDK",
            "bestName": exclude_best.get("variant"),
            "curve": _curve_points(exclude_best),
            "metrics": exclude_metrics,
            "picks": exclude_best.get("latest_rebalance", {}).get("picks", []),
        },
    }

    path = REPORTS_DIR / f"{today_stamp()}_backtest_v2_demo_deck.html"
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>回测 V2 演示 Deck</title>
  <style>
    :root {{
      --ink: #152238;
      --paper: #f7f2e8;
      --sheet: #fffaf0;
      --line: #d8c7aa;
      --muted: #62594b;
      --accent: #0f766e;
      --accent2: #b45309;
      --bad: #991b1b;
      --good: #166534;
      --tab1: #91c7b1;
      --tab2: #e7b7a5;
      --tab3: #f0cf75;
      --tab4: #9fb7d5;
      --pad: clamp(16px, 5vh, 48px);
      --h1: clamp(34px, 8vh, 76px);
      --h2: clamp(24px, 5vh, 48px);
      --body: clamp(15px, 2.4vh, 20px);
      --small: clamp(12px, 1.8vh, 15px);
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ height: 100%; overflow: hidden; }}
    body {{ margin: 0; background: #24201c; color: var(--ink); font-family: Georgia, 'Times New Roman', serif; }}
    .deck {{ width: 100vw; height: 100vh; height: 100dvh; overflow: hidden; position: relative; }}
    .slides {{ height: 100%; transition: transform 480ms cubic-bezier(.22,.61,.36,1); }}
    .slide {{
      width: 100vw;
      height: 100vh;
      height: 100dvh;
      overflow: hidden;
      padding: var(--pad);
      display: grid;
      align-content: center;
      gap: clamp(12px, 2.3vh, 24px);
      position: relative;
      background:
        linear-gradient(90deg, transparent calc(100% - 34px), var(--tab) calc(100% - 34px)),
        radial-gradient(circle at 10% 15%, rgba(15, 118, 110, .17), transparent 28%),
        linear-gradient(135deg, var(--paper), var(--sheet));
    }}
    .slide::before {{
      content: "";
      position: absolute;
      left: clamp(16px, 2vw, 30px);
      top: clamp(20px, 3vh, 38px);
      bottom: clamp(20px, 3vh, 38px);
      width: 2px;
      background: repeating-linear-gradient(to bottom, transparent 0 12px, rgba(21,34,56,.22) 12px 18px);
    }}
    h1, h2, h3, p {{ margin: 0; letter-spacing: 0; }}
    h1 {{ font-size: var(--h1); line-height: .98; max-width: 1040px; }}
    h2 {{ font-size: var(--h2); line-height: 1.03; }}
    h3 {{ font-size: clamp(18px, 3vh, 28px); }}
    p, li {{ font-size: var(--body); line-height: 1.42; }}
    .kicker {{ font: 700 var(--small)/1.2 'Trebuchet MS', sans-serif; color: var(--accent2); text-transform: uppercase; }}
    .num {{ position: absolute; right: 46px; top: 18px; font: 700 var(--small)/1 'Trebuchet MS', sans-serif; color: rgba(21,34,56,.55); }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: clamp(8px, 2vh, 14px); }}
    .two {{ display: grid; grid-template-columns: 1.05fr .95fr; gap: clamp(12px, 3vh, 24px); align-items: stretch; }}
    .metric, .panel {{ background: rgba(255, 250, 240, .83); border: 1px solid var(--line); border-radius: 8px; padding: clamp(10px, 2vh, 16px); }}
    .metric .label {{ font: 700 12px/1.2 'Trebuchet MS', sans-serif; color: var(--muted); text-transform: uppercase; }}
    .metric .value {{ font-size: clamp(23px, 4.2vh, 38px); font-weight: 700; margin-top: 8px; }}
    .muted {{ color: var(--muted); }}
    .good {{ color: var(--good); }}
    .bad {{ color: var(--bad); }}
    table {{ width: 100%; border-collapse: collapse; font: 13px/1.35 'Trebuchet MS', sans-serif; }}
    th, td {{ text-align: left; border-bottom: 1px solid var(--line); padding: 8px 7px; vertical-align: top; }}
    th {{ color: var(--muted); }}
    canvas {{ width: 100%; height: min(44vh, 390px); display: block; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .chip {{ display: inline-flex; border: 1px solid var(--line); border-radius: 99px; padding: 6px 10px; background: rgba(255,255,255,.45); font: 700 13px/1 'Trebuchet MS', sans-serif; }}
    .takeaways {{ display: grid; gap: 10px; }}
    .takeaways li {{ list-style: none; padding-left: 22px; position: relative; }}
    .takeaways li::before {{ content: "•"; position: absolute; left: 0; color: var(--accent2); font-weight: 900; }}
    .progress {{ position: fixed; left: 0; right: 0; bottom: 0; height: 5px; background: rgba(255,255,255,.28); z-index: 8; }}
    .progress-bar {{ height: 100%; width: 0; background: var(--accent); transition: width 260ms ease; }}
    .nav {{ position: fixed; right: 16px; bottom: 18px; display: flex; gap: 6px; z-index: 9; }}
    .dot {{ width: 10px; height: 10px; border-radius: 50%; border: 1px solid rgba(21,34,56,.45); background: rgba(255,255,255,.55); cursor: pointer; }}
    .dot.active {{ background: var(--accent); border-color: var(--accent); }}
    .hint {{ position: fixed; left: 16px; bottom: 16px; font: 12px/1.2 'Trebuchet MS', sans-serif; color: rgba(255,255,255,.72); z-index: 9; }}
    @media (max-width: 920px) {{ .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .two {{ grid-template-columns: 1fr; }} canvas {{ height: 32vh; }} }}
    @media (max-height: 680px) {{ :root {{ --pad: 14px; --h1: 40px; --h2: 29px; --body: 15px; }} table {{ font-size: 11px; }} th, td {{ padding: 5px; }} canvas {{ height: 30vh; }} }}
    @media (prefers-reduced-motion: reduce) {{ .slides {{ transition: none; }} }}
  </style>
</head>
<body>
  <main class="deck">
    <div class="slides" id="slides">
      <section class="slide" style="--tab: var(--tab1)" data-title="Headline">
        <span class="kicker">回测 V2 演示</span>
        <span class="num">01 / 06</span>
        <h1>回测里能达到 40% 年化目标，但代价是集中度高、波动也高。</h1>
        <div class="grid">
          <div class="metric"><div class="label">排除 SNDK 后最佳 CAGR</div><div class="value good">{_pct(exclude_metrics.get('cagr_pct'))}</div></div>
          <div class="metric"><div class="label">排除 SNDK 后最大回撤</div><div class="value bad">{_pct(exclude_metrics.get('max_drawdown_pct'))}</div></div>
          <div class="metric"><div class="label">期末权益</div><div class="value">{_usd(exclude_metrics.get('final_equity'))}</div></div>
          <div class="metric"><div class="label">最新入选</div><div class="value">{_safe(', '.join(exclude_best.get('latest_rebalance', {}).get('picks', [])))}</div></div>
        </div>
        <p class="muted">这是基于 moomoo 历史日线复权数据做的研究演示。尚未计入交易成本、滑点、税费、借券/融资约束和真实成交摩擦。</p>
      </section>

      <section class="slide" style="--tab: var(--tab2)" data-title="Leaderboard">
        <span class="kicker">策略排行榜</span>
        <span class="num">02 / 06</span>
        <h2>排除 SNDK 后，V2 的所有变体在这个窗口里仍然超过 40% 年化目标。</h2>
        <div class="panel">
          <table>
            <thead><tr><th>变体</th><th>CAGR</th><th>总收益</th><th>最大回撤</th><th>波动率</th><th>Sharpe</th><th>最新入选</th></tr></thead>
            <tbody>{_variant_rows(exclude)}</tbody>
          </table>
        </div>
        <p class="muted">更适合实盘前模拟的，不一定是 CAGR 最高的版本。回撤更低、仓位更稳的版本，才更适合第一周 paper practice。</p>
      </section>

      <section class="slide" style="--tab: var(--tab3)" data-title="Curve">
        <span class="kicker">资金曲线</span>
        <span class="num">03 / 06</span>
        <h2>增长很强，但回撤并不温柔。</h2>
        <div class="two">
          <div class="panel"><canvas id="equityChart" width="1100" height="520"></canvas></div>
          <div class="panel">
            <h3>{_safe(exclude_best.get('variant'))}</h3>
            <p>开始：{_safe(exclude_best.get('start'))}</p>
            <p>结束：{_safe(exclude_best.get('end'))}</p>
            <p>CAGR: <strong class="good">{_pct(exclude_metrics.get('cagr_pct'))}</strong></p>
            <p>最大回撤：<strong class="bad">{_top_drawdown_label(exclude_best)}</strong></p>
            <p>年化波动：<strong>{_pct(exclude_metrics.get('annual_volatility_pct'))}</strong></p>
            <p>Sharpe: <strong>{float(exclude_metrics.get('sharpe_no_rf') or 0):.2f}</strong></p>
          </div>
        </div>
      </section>

      <section class="slide" style="--tab: var(--tab4)" data-title="SNDK">
        <span class="kicker">数据质量检查</span>
        <span class="num">04 / 06</span>
        <h2>SNDK 会显著抬高回测结果，所以这版演示把它和更保守的结果分开看。</h2>
        <div class="grid">
          <div class="metric"><div class="label">含 SNDK CAGR</div><div class="value good">{_pct(include_metrics.get('cagr_pct'))}</div></div>
          <div class="metric"><div class="label">含 SNDK 期末权益</div><div class="value">{_usd(include_metrics.get('final_equity'))}</div></div>
          <div class="metric"><div class="label">排除 SNDK CAGR</div><div class="value good">{_pct(exclude_metrics.get('cagr_pct'))}</div></div>
          <div class="metric"><div class="label">排除 SNDK 期末权益</div><div class="value">{_usd(exclude_metrics.get('final_equity'))}</div></div>
        </div>
        <div class="panel">
          <h3>为什么要单独看</h3>
          <ul class="takeaways">
            <li>含 SNDK 时最佳结果达到 {_pct(include_metrics.get('cagr_pct'))} CAGR，但可能包含数据口径/价格尺度异常，或者过度依赖单一股票。</li>
            <li>排除 SNDK 后仍然超过目标，但最新入选依然很集中：{_safe(', '.join(exclude_best.get('latest_rebalance', {}).get('picks', [])))}。</li>
            <li>下一版回测要加入交易成本、滑点、宏观闸门和 guard pipeline 约束。</li>
          </ul>
        </div>
      </section>

      <section class="slide" style="--tab: var(--tab1)" data-title="Picks">
        <span class="kicker">最新组合信号</span>
        <span class="num">05 / 06</span>
        <h2>这些最新入选只是模拟盘观察池，不是交易指令。</h2>
        <div class="chips">
          {"".join(f'<span class="chip">{_safe(symbol)}</span>' for symbol in exclude_best.get('latest_rebalance', {}).get('picks', []))}
        </div>
        <div class="panel">
          <h3>任何建仓 intent 之前，都必须先过人机协作闸门</h3>
          <ul class="takeaways">
            <li>确认每个 ticker 的 moomoo 数据干净，并且流动性足够。</li>
            <li>写清楚 thesis、入场触发、失效条件和最大风险。</li>
            <li>跑宏观 guard：当前宏观偏鹰，所以仓位要保守。</li>
            <li>以上都通过后，才生成 Trading-as-Git 的 staged intent。</li>
          </ul>
        </div>
      </section>

      <section class="slide" style="--tab: var(--tab2)" data-title="Next">
        <span class="kicker">下一步迭代</span>
        <span class="num">06 / 06</span>
        <h2>一周模拟盘建议先用低风险变体做 baseline。</h2>
        <div class="panel">
          <ul class="takeaways">
            <li>Baseline 候选：<strong>v2_smh30_top4_70pct</strong>，因为它仍有 {_pct([v for v in exclude.get('variants', []) if v.get('variant') == 'v2_smh30_top4_70pct'][0].get('metrics', {}).get('cagr_pct') if [v for v in exclude.get('variants', []) if v.get('variant') == 'v2_smh30_top4_70pct'] else None)} CAGR，但最大回撤低于最高杠杆版本。</li>
            <li>第一周模拟盘不要直接用 130% margin sim。</li>
            <li>每天跑 monitor，每周看 review deck；只有 guard pipeline 复核后才使用 staged intent。</li>
            <li>V3 要加入成本、宏观 overlay、watchlist 排除项，以及更真实的再平衡成交假设。</li>
          </ul>
        </div>
      </section>
    </div>
    <div class="nav" id="navDots"></div>
    <div class="progress"><div class="progress-bar" id="progressBar"></div></div>
    <div class="hint">← → / 空格 / 滑动</div>
  </main>
  <script>
    const deckData = {json.dumps(data, ensure_ascii=False)};
    class Deck {{
      constructor() {{
        this.track = document.getElementById('slides');
        this.slides = [...document.querySelectorAll('.slide')];
        this.nav = document.getElementById('navDots');
        this.progress = document.getElementById('progressBar');
        this.index = 0;
        this.touchX = null;
        this.nav.innerHTML = this.slides.map((slide, i) => `<button class="dot" aria-label="${{slide.dataset.title}}" data-i="${{i}}"></button>`).join('');
        this.nav.querySelectorAll('button').forEach(dot => dot.addEventListener('click', () => this.go(Number(dot.dataset.i))));
        document.addEventListener('keydown', e => {{
          if (['ArrowRight','PageDown',' '].includes(e.key)) this.next();
          if (['ArrowLeft','PageUp'].includes(e.key)) this.prev();
        }});
        document.addEventListener('wheel', e => {{
          if (Math.abs(e.deltaY) < 30) return;
          e.deltaY > 0 ? this.next() : this.prev();
        }}, {{ passive: true }});
        document.addEventListener('touchstart', e => {{ this.touchX = e.touches[0].clientX; }}, {{ passive: true }});
        document.addEventListener('touchend', e => {{
          if (this.touchX === null) return;
          const dx = e.changedTouches[0].clientX - this.touchX;
          if (Math.abs(dx) > 40) dx < 0 ? this.next() : this.prev();
          this.touchX = null;
        }}, {{ passive: true }});
        this.go(0);
      }}
      go(i) {{
        this.index = Math.max(0, Math.min(i, this.slides.length - 1));
        this.track.style.transform = `translateY(${{-100 * this.index}}vh)`;
        this.progress.style.width = `${{((this.index + 1) / this.slides.length) * 100}}%`;
        this.nav.querySelectorAll('.dot').forEach((d, idx) => d.classList.toggle('active', idx === this.index));
      }}
      next() {{ this.go(this.index + 1); }}
      prev() {{ this.go(this.index - 1); }}
    }}
    new Deck();

    function drawCurve() {{
      const canvas = document.getElementById('equityChart');
      const ctx = canvas.getContext('2d');
      const curves = [
        {{ label: deckData.exclude.label, data: deckData.exclude.curve, color: '#0f766e' }},
        {{ label: deckData.include.label, data: deckData.include.curve, color: '#b45309' }}
      ];
      const values = curves.flatMap(c => c.data.map(p => Number(p.equity))).filter(Number.isFinite);
      const min = Math.min(...values);
      const max = Math.max(...values);
      const pad = 54;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#fffaf0';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.strokeStyle = '#d8c7aa';
      ctx.lineWidth = 1;
      for (let i = 0; i <= 4; i++) {{
        const y = pad + (canvas.height - pad * 2) * i / 4;
        ctx.beginPath();
        ctx.moveTo(pad, y);
        ctx.lineTo(canvas.width - pad, y);
        ctx.stroke();
      }}
      ctx.fillStyle = '#62594b';
      ctx.font = '22px Trebuchet MS';
      ctx.fillText('资金曲线', pad, 34);
      curves.forEach((curve, idx) => {{
        const n = curve.data.length;
        ctx.strokeStyle = curve.color;
        ctx.lineWidth = 4;
        ctx.beginPath();
        curve.data.forEach((point, i) => {{
          const x = pad + (canvas.width - pad * 2) * (n <= 1 ? 0 : i / (n - 1));
          const y = canvas.height - pad - (Number(point.equity) - min) / Math.max(1, max - min) * (canvas.height - pad * 2);
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }});
        ctx.stroke();
        ctx.fillStyle = curve.color;
        ctx.fillRect(canvas.width - pad - 170, 24 + idx * 28, 18, 5);
        ctx.fillText(curve.label, canvas.width - pad - 145, 34 + idx * 28);
      }});
      ctx.fillStyle = '#62594b';
      ctx.font = '18px Trebuchet MS';
      ctx.fillText('$' + Math.round(min).toLocaleString(), 10, canvas.height - pad);
      ctx.fillText('$' + Math.round(max).toLocaleString(), 10, pad + 8);
    }}
    drawCurve();
    window.addEventListener('resize', drawCurve);
  </script>
</body>
</html>"""
    path.write_text(html_doc, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Chinese demo HTML deck from backtest v2 JSON outputs.")
    parser.add_argument("--include", default=str(DEFAULT_INCLUDE))
    parser.add_argument("--exclude", default=str(DEFAULT_EXCLUDE))
    args = parser.parse_args()
    path = build_demo(Path(args.include), Path(args.exclude))
    print(json.dumps({"status": "completed", "html": str(path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
