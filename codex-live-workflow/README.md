# Agentic Investor Live Workflow Transfer

This package is a sanitized transfer kit for the live Codex investment workflow.

It includes:

- `skill/agentic-investor-live-workflow`: the Codex skill to install on another machine.
- `template/agentic-investor`: the workflow scripts and configuration templates.
- `integrations/weixin`: portable WeChat bridge scripts.
- `docs/transfer-24h-machine.md`: setup checklist for the always-on machine.

It intentionally excludes:

- `.env`
- GitHub/OpenAI/Firebase/API tokens
- moomoo passwords or trading unlock data
- real account snapshots
- WeChat recipient ids and context tokens
- private logs, reports, trade logs, and cached market/account data

Default policy: advisory-only, read-only broker inspection, no unlock, no broker order placement, no cancel/modify order calls.

