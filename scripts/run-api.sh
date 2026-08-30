#!/usr/bin/env bash
# Run the PatchPilot orchestrator API.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
[ -f .env ] && set -a && . ./.env && set +a
exec .venv/bin/python -m uvicorn patchpilot.main:app \
  --app-dir apps/api --host 127.0.0.1 --port "${PATCHPILOT_API_PORT:-8080}" "$@"
