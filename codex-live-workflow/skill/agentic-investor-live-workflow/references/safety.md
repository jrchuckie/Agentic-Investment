# Safety And Privacy

This workflow is designed for investment advice, monitoring, and audit notes. It is not a broker executor.

Non-negotiable boundaries:

- No trading unlock flow.
- No broker order placement.
- No order cancellation or modification.
- No storage of moomoo trading passwords.
- No publication of real account snapshots, cash balances, account ids, WeChat recipient ids, context tokens, service account JSON, or `.env`.

Allowed read-only moomoo fields:

- account list metadata needed to select the already logged-in account
- cash, buying power, total assets, market value
- positions, cost, last or nominal price, unrealized P/L, sellable quantity
- open orders for awareness only

If OpenD is unavailable, treat it as a data refresh blockage, not a strategy conclusion. Use cached local artifacts only if clearly labeled as cached.

If WeChat push fails, report it separately from strategy logic.

