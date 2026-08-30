#!/usr/bin/env bash
# Start every part of PatchPilot and leave it ready to demonstrate.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
[ -f .env ] && set -a && . ./.env && set +a
mkdir -p .run

echo "==> 1/5 harness"
bash scripts/run-harness.sh

echo "==> 2/5 simulator and MCP servers"
bash scripts/run-services.sh

echo "==> 3/5 configuring the harness"
.venv/bin/python scripts/setup_trueforge.py
.venv/bin/python scripts/setup_agents.py

echo "==> 4/5 orchestrator API"
if curl -s -o /dev/null -m 2 "http://127.0.0.1:${PATCHPILOT_API_PORT:-8080}/api/health"; then
  echo "  = API already running"
else
  bash scripts/run-api.sh > .run/api.log 2>&1 &
  echo $! > .run/api.pid
  for _ in $(seq 1 40); do
    curl -s -o /dev/null -m 1 "http://127.0.0.1:${PATCHPILOT_API_PORT:-8080}/api/health" && break
    sleep 0.5
  done
  echo "  ✓ API is up"
fi

echo "==> 5/5 web UI"
if curl -s -o /dev/null -m 2 "http://127.0.0.1:${PATCHPILOT_WEB_PORT:-3000}"; then
  echo "  = web already running"
else
  (cd apps/web && npm run dev > ../../.run/web.log 2>&1 & echo $! > ../../.run/web.pid)
  for _ in $(seq 1 60); do
    curl -s -o /dev/null -m 1 "http://127.0.0.1:${PATCHPILOT_WEB_PORT:-3000}" && break
    sleep 0.5
  done
  echo "  ✓ web is up"
fi

echo
echo "PatchPilot is running:"
echo "  UI       http://localhost:${PATCHPILOT_WEB_PORT:-3000}"
echo "  API      http://localhost:${PATCHPILOT_API_PORT:-8080}"
echo "  harness  http://localhost:${TRUEFORGE_PORT:-8790}"
echo
echo "Run scripts/reset-demo.sh before demonstrating, then click Investigate."
