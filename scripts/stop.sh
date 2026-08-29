#!/usr/bin/env bash
# Stop everything started by scripts/run-services.sh.
set -uo pipefail
cd "$(dirname "$0")/.."
[ -d .run ] || { echo "nothing to stop"; exit 0; }
for pidfile in .run/*.pid; do
  [ -e "$pidfile" ] || continue
  name="$(basename "$pidfile" .pid)"
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null && echo "  - stopped $name (pid $pid)"
  fi
  rm -f "$pidfile"
done
