#!/usr/bin/env bash
# Spins up real TS3 + TS6 servers in docker and runs the integration suite
# against both. Placeholder until the integration tier lands (M4/M7): exits
# green when no integration tests are collected yet.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! uv run --frozen pytest -m integration --collect-only -q >/dev/null 2>&1; then
  echo "No integration tests collected yet - skipping (placeholder)."
  exit 0
fi

echo "Integration harness not implemented yet but integration tests exist." >&2
exit 1
