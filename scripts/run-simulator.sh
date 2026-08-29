#!/usr/bin/env bash
# Run the simulated production checkout service.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${SIMULATOR_PORT:-8000}"
exec .venv/bin/python -m uvicorn app.main:app \
  --app-dir simulator/checkout-service --host 127.0.0.1 --port "$PORT" "$@"
