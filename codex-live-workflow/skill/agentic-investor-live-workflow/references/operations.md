# Operations

## Daily Morning Brief

Run a read-only account refresh, update the market snapshot, check event radar, evaluate position risk, and push a short Chinese brief to WeChat.

Preferred command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_opening_decision_push.ps1 "morning brief"
```

## Intraday Monitor

Use a 15-minute cadence when requested. The conclusion must be pushed even when the answer is `hold`, `wait`, or `no action`.

Required section order for recurring monitor pushes:

1. alerts
2. market snapshot
3. VIX/10Y/USD-CNH
4. paper NAV/positions
5. scenario
6. new fill
7. eventRadar
8. dashboard/Firebase
9. Top3

## Reduction Guard

Use this when the user asks whether to reduce, exit, or protect the portfolio:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_reduction_guard.ps1 --push --force-push
```

Outputs:

- `data/risk/reduction_guard_latest.json`
- `reports/YYYY-MM-DD_moomoo_reduction_guard.md`
- optional WeChat push

## Dashboard

When asked to refresh dashboard, use the existing pipeline rather than improvising:

```powershell
python scripts\run_task.py dashboard_snapshot
python scripts\publish_dashboard_firestore.py
```

If Firebase credentials are missing, return a local dashboard/status artifact and say publish is blocked by credentials.

## Failure Handling

- `WSAECONNREFUSED` or port `11111` closed: OpenD not reachable.
- Account refresh failure with WeChat success: push succeeded, data is stale or cached.
- WeChat failure with account refresh success: strategy result exists locally, push channel needs repair.
- Network/search failure: avoid looping; produce a cached-basis conclusion with a stale-data warning.

