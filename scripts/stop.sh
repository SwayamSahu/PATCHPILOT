#!/usr/bin/env bash
# Stop everything started by scripts/run-services.sh.
set -uo pipefail
cd "$(dirname "$0")/.."
[ -d .run ] || { echo "nothing to stop"; exit 0; }
# A PID file outlives the process it names. If that process exited and the OS
# recycled its id, killing it blindly terminates whatever unrelated program now
# holds it, so the command line is checked before signalling anything.
for pidfile in .run/*.pid; do
  [ -e "$pidfile" ] || continue
  name="$(basename "$pidfile" .pid)"
  pid="$(cat "$pidfile" 2>/dev/null || true)"
  case "$pid" in
    ''|*[!0-9]*) rm -f "$pidfile"; continue ;;
  esac

  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pidfile"
    continue
  fi

  cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
  case "$cmd" in
    *uvicorn*|*mcp_servers*|*trueforge*|*next*|*patchpilot*)
      kill "$pid" 2>/dev/null && echo "  - stopped $name (pid $pid)"
      ;;
    *)
      echo "  ! skipping $name: pid $pid is now '${cmd:-unknown}', not a PatchPilot process" >&2
      ;;
  esac
  rm -f "$pidfile"
done
