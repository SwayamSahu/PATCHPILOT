#!/usr/bin/env bash
# Start the simulated production service and the three MCP servers.
#
# Each writes to .run/<name>.log and its pid to .run/<name>.pid, so
# scripts/stop.sh can shut the whole set down cleanly.
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
[ -f .env ] && set -a && . ./.env && set +a

mkdir -p .run
PY=".venv/bin/python"
[ -x "$PY" ] || { echo "error: .venv not found. Run 'make setup' first." >&2; exit 1; }

start() {
  local name="$1"; shift
  if [ -f ".run/$name.pid" ] && kill -0 "$(cat ".run/$name.pid")" 2>/dev/null; then
    echo "  = $name already running (pid $(cat ".run/$name.pid"))"
    return
  fi
  "$@" > ".run/$name.log" 2>&1 &
  echo $! > ".run/$name.pid"
  echo "  + $name (pid $!)"
}

# A connection that completes is not a healthy service: a 404 or 500 from a
# failed or unrelated process would otherwise be reported as ready.
#
# The two service kinds need different proof. An HTTP service must answer 200. An
# MCP endpoint answers a bare GET with 400 and a JSON-RPC error ("Missing session
# ID"), which is a live server correctly refusing an incomplete request - so the
# check looks for the protocol in the body rather than trusting the status alone.
# A stray 400 from an unrelated process will not be JSON-RPC.
wait_for() {
  local name="$1" url="$2" kind="${3:-http}" code body
  for _ in $(seq 1 40); do
    body="$(curl -s -m 1 -o /tmp/.pp-probe -w '%{http_code}' "$url" 2>/dev/null || echo 000)"
    code="$body"
    if [ "$kind" = "mcp" ]; then
      if grep -q '"jsonrpc"' /tmp/.pp-probe 2>/dev/null; then
        echo "  ✓ $name is up (MCP responding)"; rm -f /tmp/.pp-probe; return 0
      fi
    elif [ "$code" = "200" ]; then
      echo "  ✓ $name is up"; rm -f /tmp/.pp-probe; return 0
    fi
    sleep 0.25
  done
  echo "  ✗ $name did not come up (last HTTP $code); see .run/$name.log" >&2
  rm -f /tmp/.pp-probe
  return 1
}

echo "==> starting services"
start simulator "$PY" -m uvicorn app.main:app \
  --app-dir simulator/checkout-service --host 127.0.0.1 --port "${SIMULATOR_PORT:-8000}"
start mcp-telemetry  "$PY" -m mcp_servers.telemetry.server
start mcp-repository "$PY" -m mcp_servers.repository.server
start mcp-deployment "$PY" -m mcp_servers.deployment.server

echo "==> waiting for health"
wait_for simulator      "http://127.0.0.1:${SIMULATOR_PORT:-8000}/health"
wait_for mcp-telemetry  "http://127.0.0.1:${MCP_TELEMETRY_PORT:-8101}/mcp" mcp
wait_for mcp-repository "http://127.0.0.1:${MCP_REPOSITORY_PORT:-8102}/mcp" mcp
wait_for mcp-deployment "http://127.0.0.1:${MCP_DEPLOYMENT_PORT:-8103}/mcp" mcp
echo "==> all services running"
