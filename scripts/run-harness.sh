#!/usr/bin/env bash
# Start the TrueForge harness in local standalone mode (SQLite, no other infra).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
[ -f .env ] && set -a && . ./.env && set +a
mkdir -p .trueforge .run

PORT_TF="${TRUEFORGE_PORT:-8790}"
if curl -s -o /dev/null -m 2 "http://localhost:${PORT_TF}/api/v1/capabilities"; then
  echo "  = TrueForge already running on :${PORT_TF}"
  exit 0
fi

echo "==> starting TrueForge on :${PORT_TF} (first run downloads the package)"
SQLITE_PATH="$PWD/.trueforge/db.sqlite" PORT="$PORT_TF" \
  npx -y @truefoundry/trueforge@latest > .trueforge/server.log 2>&1 &
echo $! > .run/trueforge.pid

for _ in $(seq 1 120); do
  if curl -s -o /dev/null -m 2 "http://localhost:${PORT_TF}/api/v1/capabilities"; then
    echo "  ✓ TrueForge is up at http://localhost:${PORT_TF}"
    exit 0
  fi
  sleep 1
done
echo "  ✗ TrueForge did not start; see .trueforge/server.log" >&2
exit 1
