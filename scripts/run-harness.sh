#!/usr/bin/env bash
# Start the TrueForge harness in local standalone mode (SQLite, no other infra).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
[ -f .env ] && set -a && . ./.env && set +a
mkdir -p .trueforge .run

# Derive the port from the same setting the Python clients use, so changing
# TRUEFORGE_API_BASE moves the server and the probes together instead of starting
# the harness on one port and talking to it on another.
API_BASE="${TRUEFORGE_API_BASE:-http://localhost:8790/api/v1}"
PORT_TF="$(printf '%s' "$API_BASE" | sed -E 's#^[a-z]+://[^:/]+:?([0-9]*).*#\1#')"
[ -n "$PORT_TF" ] || PORT_TF=8790

ready() { [ "$(curl -s -o /dev/null -m 2 -w '%{http_code}' "$API_BASE/capabilities" || echo 000)" = "200" ]; }

if ready; then
  echo "  = TrueForge already running on :${PORT_TF}"
  exit 0
fi

echo "==> starting TrueForge on :${PORT_TF} (first run downloads the package)"
SQLITE_PATH="$PWD/.trueforge/db.sqlite" PORT="$PORT_TF" \
  npx -y @truefoundry/trueforge@latest > .trueforge/server.log 2>&1 &
echo $! > .run/trueforge.pid

for _ in $(seq 1 120); do
  if ready; then
    echo "  ✓ TrueForge is up at http://localhost:${PORT_TF}"
    exit 0
  fi
  sleep 1
done
echo "  ✗ TrueForge did not start; see .trueforge/server.log" >&2
exit 1
