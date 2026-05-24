---
name: agentic-investor-live-workflow
description: Replicate and run the user's advisory-only Agentic Investor workflow on a 24-hour Codex machine, including moomoo read-only real-account review, intraday monitoring, reduction guard, dashboard refresh, WeChat push, and memory-derived operating conventions. Use when asked to set up, restore, monitor, review, push, or debug this investment workflow.
---

# Agentic Investor Live Workflow

Use this skill for the user's live U.S. equities monitoring workflow. Default language is Simplified Chinese. Default output is decision-shaped: conclusion first, then the evidence and exact triggers.

## Hard Safety Rules

- Advisory-only by default.
- Do not unlock trading.
- Do not place, cancel, or modify broker orders.
- Do not request or store moomoo trading passwords.
- Do not publish `.env`, account snapshots, WeChat target ids, tokens, private logs, reports, or service account files.
- Real moomoo account access is read-only and only for assets, cash, buying power, positions, P/L, sellable quantity, and open orders.

Read `references/safety.md` before touching any broker, WeChat, cron, or publishing workflow.

## First Checks

1. Find the workspace. Preferred path on Windows is `%USERPROFILE%\Documents\New project\agentic-investor`.
2. Check that moomoo OpenD is running at `127.0.0.1:11111`.
3. Check that the WeChat sender exists at `%USERPROFILE%\Documents\Codex\weixin-send.mjs`.
4. Check for `%USERPROFILE%\.codex\weixin-bridge\latest-target.json`; if it is missing, ask the user to send one message to the WeChat bot first.
5. Prefer the project-local Python runtime `.venv-openbb\Scripts\python.exe`; fall back to `python` only if needed.

## Core Commands

Use these from the `agentic-investor` workspace:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_opening_decision_push.ps1 "15m decision"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_reduction_guard.ps1 --push --force-push
python scripts\run_task.py intraday_monitor
python scripts\run_task.py dashboard_snapshot
python scripts\generate_wechat_moomoo_daily_brief.py
```

For direct WeChat tests:

```powershell
node %USERPROFILE%\Documents\Codex\weixin-send.mjs "测试：Codex 微信联通正常"
node %USERPROFILE%\Documents\Codex\codex-push-weixin.mjs "给我一版简短系统自检"
```

## Operating Shape

- Morning brief: real-account read-only refresh, market snapshot, event radar, position risk, top actions, push to WeChat.
- Intraday monitor: run every 15 minutes during the requested window; always push the conclusion, even if the conclusion is hold or wait.
- Reduction guard: produce explicit `EXIT`, `TRIM`, `WATCH`, or `HOLD`; include trigger price, size, invalidation, and valid window before calling anything executable.
- Dashboard refresh: use the existing publish pipeline; when the user asks for dashboard, include the human-openable entry point.
- If OpenD fails, separate data refresh failure from WeChat push failure. Push the best available conclusion and say the account refresh was blocked.

Read `references/operations.md` for the detailed loop and `references/memory.md` for the distilled conventions from prior runs.

