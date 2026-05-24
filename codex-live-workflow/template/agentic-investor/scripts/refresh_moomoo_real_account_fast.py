from __future__ import annotations

import json
import os

from moomoo_real_account import fetch_real_account_snapshot, write_outputs


def main() -> int:
    snapshot = fetch_real_account_snapshot(
        host=os.environ.get("MOOMOO_OPEND_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=int(os.environ.get("MOOMOO_OPEND_PORT", "11111").strip() or "11111"),
        security_firm=os.environ.get("MOOMOO_SECURITY_FIRM", "FUTUINC").strip() or "FUTUINC",
        expected_trdmarket=os.environ.get("MOOMOO_EXPECTED_TRDMARKET", "US").strip() or "US",
    )
    report, latest = write_outputs(snapshot)
    print(
        json.dumps(
            {
                "task": "refresh_moomoo_real_account_fast",
                "status": snapshot.get("status"),
                "timestamp": snapshot.get("timestamp"),
                "positions": len(((snapshot.get("positions") or {}).get("records") or [])),
                "openOrders": len(((snapshot.get("orders") or {}).get("records") or [])),
                "latest": latest,
                "report": report,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if snapshot.get("status") in {"PASS", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
