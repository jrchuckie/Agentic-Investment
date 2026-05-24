#!/usr/bin/env bash
set -euo pipefail

TASK="${1:?task required}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK_DIR="${AGENTIC_INVESTOR_LOCK_DIR:-/tmp/agentic-investor.lock}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  date -Is
  echo "agentic-investor is already running; skipped ${TASK}"
  exit 0
fi

cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

cd "$ROOT"
"$PYTHON_BIN" scripts/run_task.py "$TASK"
