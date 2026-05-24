# Distilled Memory

Default user preference:

- Use Chinese unless the user switches language.
- Be concrete: buy/sell/hold/wait, trigger, stop, invalidation, size, and valid window.
- Do not hide behind generic VWAP language; convert it to exact numeric levels when giving intraday advice.
- For Top 3 stock pitch, give a concrete recommendation set rather than only saying continue to observe.
- Keep event radar cross-symbol, not just current holdings.

Moomoo workflow:

- Real-account checks are read-only only.
- Set `MOOMOO_REAL_ACCOUNT_READ=1` for read-only account refreshes.
- OpenD endpoint is normally `127.0.0.1:11111`.
- Repeated connection refused means OpenD is the blocker; it is not a strategy result.
- Use `data/broker/moomoo/real_account_latest.json` only as a local private artifact. Never publish it.

WeChat workflow:

- Direct push: `node %USERPROFILE%\Documents\Codex\weixin-send.mjs "通知内容"`.
- Codex-then-push: `node %USERPROFILE%\Documents\Codex\codex-push-weixin.mjs "让 Codex 做的任务"`.
- If no push arrives, check `%USERPROFILE%\.codex\weixin-bridge\latest-target.json` and ask the user to send the bot one message.

Portfolio review workflow:

- Combine price/technical, events, industry, earnings, and portfolio risk.
- Distinguish new buy candidates from reduce/exit decisions.
- Avoid very small, illiquid names for new entries unless the user explicitly accepts that risk.
- For speculative growth holdings, prioritize downside control and event catalysts.

