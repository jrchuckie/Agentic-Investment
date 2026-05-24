#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CACHE="$ROOT/vendor/pip-cache"
WHEELHOUSE="$CACHE/wheelhouse"
VENDOR_PYTHON="$ROOT/vendor/python"

mkdir -p "$CACHE"

PIP_SOURCE_ARGS=(--cache-dir "$CACHE" --upgrade)
if [ -d "$WHEELHOUSE" ]; then
  PIP_SOURCE_ARGS=(--no-index --find-links "$WHEELHOUSE" "${PIP_SOURCE_ARGS[@]}")
fi

if [ -d "$VENDOR_PYTHON" ]; then
  while IFS= read -r -d '' item; do
    rm -rf "$item"
  done < <(find "$VENDOR_PYTHON" -maxdepth 1 \( -name "openbb" -o -name "openbb-*" -o -name "openbb_*" \) -print0)
fi

PREVIOUS_PYTHONPATH="${PYTHONPATH:-}"
PREVIOUS_VENDOR_FLAG="${AGENTIC_ENABLE_VENDOR_PYTHON:-}"

restore_env() {
  export PYTHONPATH="$PREVIOUS_PYTHONPATH"
  export AGENTIC_ENABLE_VENDOR_PYTHON="$PREVIOUS_VENDOR_FLAG"
}
trap restore_env EXIT

export AGENTIC_ENABLE_VENDOR_PYTHON=0

if [ "${AGENTIC_OPENBB_UPGRADE_PIP:-0}" = "1" ]; then
  "$PYTHON_BIN" -m pip install --upgrade pip
fi

"$PYTHON_BIN" -m pip install openbb --no-deps "${PIP_SOURCE_ARGS[@]}"
"$PYTHON_BIN" -m pip install \
  openbb-core \
  openbb-equity \
  openbb-index \
  openbb-fixedincome \
  openbb-currency \
  openbb-federal-reserve \
  openbb-fred \
  openbb-yfinance \
  "${PIP_SOURCE_ARGS[@]}"

"$PYTHON_BIN" -c "import openbb; build = getattr(openbb, 'build', None); build() if callable(build) else None"
"$PYTHON_BIN" "$ROOT/scripts/run_task.py" openbb_smoke
