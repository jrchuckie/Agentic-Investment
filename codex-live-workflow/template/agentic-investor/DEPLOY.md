# Agentic Investor - Deployment Notes

This folder is now advisory-first. Scheduled jobs generate reports, checklists, and order drafts only. They do not run natural-language trading commands and do not place live orders.

For the current OpenClaw/Firebase dashboard deployment, use `OPENCLAW_TRANSFER.zh.md` as the primary SOP. It includes the latest dashboard, OpenBB, event radar, Firestore, cron, and first-night validation steps.

## 1. Copy the folder

Put the folder in the runtime workspace:

```bash
scp -r agentic-investor/ user@mac-mini-ip:~/.openclaw/workspace/
```

If you want Codex to auto-discover it as a skill, also place it under your Codex skills directory, for example:

```bash
cp -R agentic-investor ~/.codex/skills/
```

## 2. Confirm Python

The advisory scripts use only the Python standard library.

```bash
cd ~/.openclaw/workspace/agentic-investor
python3 scripts/run_task.py pre_market_scan
```

The command should write a report under `reports/` and append one record to `trade-log.json`.

## 3. Keep live trading credentials out

Do not put `MOOMOO_TRADE_PASSWORD` in `.zshrc`, cron, this repository, or `state.json`.

Current safety defaults:

```json
{
  "mode": "SIMULATE",
  "execution_mode": "ADVISORY_ONLY",
  "allow_order_placement": false
}
```

For real accounts, this skill should generate approval packets only. Add a separate, reviewed executor before considering broker-side execution.

## 4. Install advisory cron

Review `agentic-investor-cron.txt`, especially `PYTHON_BIN` and `AGENTIC_INVESTOR_HOME`, then install:

```bash
crontab ~/.openclaw/workspace/agentic-investor/agentic-investor-cron.txt
crontab -l
```

The cron file calls `bash scripts/cron_entry.sh <task>`, which uses a lock directory to avoid overlapping runs.

Current daily intraday cron should call `intraday_monitor` every 15 minutes during US regular trading hours. That task now refreshes market data, paper mark-to-market, conditional playbook, paper fills, intel/social/event radar, order intents, dashboard snapshot, and Firestore publish in one advisory-only pass.

## 5. Manual tasks

```bash
python3 scripts/run_task.py pre_market_scan
python3 scripts/run_task.py trading_signals
python3 scripts/run_task.py watchlist_review
python3 scripts/run_task.py health_check
python3 scripts/run_task.py macro_regime
python3 scripts/run_task.py earnings_event_risk
python3 scripts/run_task.py order_intents
python3 scripts/run_task.py intel_monitor
python3 scripts/run_task.py social_sentiment_feed
python3 scripts/run_task.py intraday_monitor
python3 scripts/run_task.py dashboard_snapshot
python3 scripts/run_task.py firebase_publish_snapshot
python3 scripts/run_task.py research_committee
python3 scripts/run_task.py review_dashboard
python3 scripts/run_task.py mid_day_review
python3 scripts/run_task.py post_market_summary
python3 scripts/run_task.py fund_holdings_tracker
python3 scripts/backtest_v2.py
python3 scripts/run_task.py congress_trades_tracker
python3 scripts/run_task.py weekly_rebalance
python3 scripts/run_task.py monthly_review
python3 scripts/run_task.py emergency_stop
```

## 5b. Watchlist and macro workflow

Use the watchlist manager when the user sends a ticker, Reddit/X link, or social-buzz idea:

```bash
python3 scripts/watchlist_manager.py add --symbol INTC --thesis "user/social idea"
python3 scripts/watchlist_manager.py exclude --symbol INTC --reason "user rejected"
python3 scripts/watchlist_manager.py review
```

Use the macro checker for Fed/rate-path overlays:

```bash
python3 scripts/macro_regime.py review
python3 scripts/macro_regime.py set-fed --target-range "..." --bias HAWKISH --source "Federal Reserve"
```

These tasks are advisory-only and never place orders.

## 5d. Intelligence monitor and interactive reviews

Use the intelligence monitor for AI/finance thought leaders, Reddit/RSS sources, and watchlist-related headlines:

```bash
python3 scripts/intel_monitor.py
python3 scripts/intel_monitor.py --fetch
python3 scripts/intel_monitor.py --manual-title "X thread" --manual-url "https://x.com/..." --manual-text "..."
```

Direct X monitoring requires an official API token. Without it, paste X links manually for review.

Build interactive HTML review dashboards:

```bash
python3 scripts/review_dashboard.py --period weekly
python3 scripts/review_dashboard.py --period quarterly
```

## 8. Default publish target

Default external repo for demos and selected advisory reports:

```text
https://github.com/jrchuckie/Agentic-Investment
```

Before publishing, exclude broker credentials, account IDs, account snapshots, local logs, and any private data. The intended publishable artifacts are HTML demo decks, HTML review dashboards, and selected advisory Markdown reports.

For automatic GitHub Pages management, configure a fine-grained token with:

- `Contents: Read and write`
- `Pages: Read and write`
- `Administration: Read and write`

Then use:

```bash
python3 scripts/manage_github_pages.py status
python3 scripts/manage_github_pages.py ensure --branch main --path /docs
python3 scripts/manage_github_pages.py build
```

## 5c. Trading-as-Git advisory intents

Use order intents when a candidate is ready for an auditable "possible trade" packet:

```bash
python3 scripts/order_intent.py stage --symbol INTC --side BUY --notional 1000 --rationale "..." --entry-trigger "..." --invalidation "..."
python3 scripts/order_intent.py list
python3 scripts/order_intent.py commit --intent-id <id>
python3 scripts/order_intent.py reject --intent-id <id> --reason "user rejected"
python3 scripts/order_intent.py review
```

The guard pipeline checks safety mode, excluded symbols, thesis, macro overlay, position size, and read-only market data. A committed intent is still advisory-only.

## 5a. Fund manager holdings tracker

The fund tracker pulls public holdings disclosures, writes `data/fund_holdings/latest.json`, and feeds those symbols into `scripts/backtest_v2.py`.

```bash
python3 scripts/fund_holdings_tracker.py update --allow-stale
python3 scripts/backtest_v2.py
```

For SEC 13F sources, set a real contact string before running on a server:

```bash
export SEC_USER_AGENT="agentic-investor your.email@example.com"
```

The backtest uses the latest holdings feed as an idea-generation overlay. It is not a clean point-in-time historical signal until the tracker has accumulated enough dated snapshots.

## 5e. Earnings and congressional disclosure trackers

Run the earnings/event-risk tracker before the US open. It writes `data/events/earnings_latest.json` and feeds the option guard so near-term earnings symbols can block new calls, puts, covered calls, cash-secured puts, and other option intents.

```bash
python3 scripts/run_task.py earnings_event_risk
```

Run the congressional disclosure tracker monthly and quarterly. It writes `data/congress_trades/latest.json`; Pelosi and other tracked members are idea sources only, and official House/Senate verification is required before using a signal in a thesis.

```bash
python3 scripts/run_task.py congress_trades_tracker
```

## 6. Emergency stop

Run:

```bash
python3 scripts/run_task.py emergency_stop
```

This sets `paused: true`, disables order placement, and writes an emergency report. Because the current scripts are advisory-only, they do not call broker cancellation APIs. Verify open orders manually in moomoo/OpenD if any broker-side executor exists.

## 7. Logs and reports

- Reports: `reports/YYYY-MM-DD_<task>.md`
- Audit log: `trade-log.json`
- Cron logs: `/tmp/agentic-investor-*.log`
