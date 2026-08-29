#!/usr/bin/env bash
# Run the PatchPilot test suite.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  echo "error: .venv not found. Run 'make setup' first." >&2
  exit 1
fi

.venv/bin/python -m pytest "$@"
