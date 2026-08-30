#!/usr/bin/env bash
# Return everything to the opening scene of the demo.
#
# After this runs, production is broken again in exactly the same way, the
# agent's working clone is back at the base branch, no approval is outstanding,
# and the workflow timeline is empty. The demo can be given repeatedly.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
[ -f .env ] && set -a && . ./.env && set +a

API="http://127.0.0.1:${PATCHPILOT_API_PORT:-8080}"
SIM="http://127.0.0.1:${SIMULATOR_PORT:-8000}"

echo "==> resetting the simulated production environment"
if curl -s -o /dev/null -m 3 "$SIM/health"; then
  curl -s -X POST "$SIM/_control/reset" > /dev/null
  echo "    production is broken again on 4c21"
else
  echo "    simulator not running; it will seed itself on next start"
fi

echo "==> clearing approvals"
rm -f simulator/.state/approvals.json simulator/.state/pending_deployments.json
echo "    no approval is outstanding"

echo "==> clearing the workflow timeline"
rm -f .run/workflow.json
if curl -s -o /dev/null -m 3 "$API/api/health"; then
  curl -s -X POST "$API/api/reset" > /dev/null
fi

echo "==> restoring the agent's working clone"
bash scripts/prepare-workrepo.sh > /dev/null
echo "    clone is back on ${PATCHPILOT_BASE_BRANCH:-main}"

echo "==> deleting any fix branch left on GitHub"
BRANCH="fix/checkout-discount"
if [ -n "${GITHUB_TOKEN:-}" ] && [ -n "${GITHUB_OWNER:-}" ] && [ -n "${GITHUB_REPO:-}" ]; then
  curl -s -X DELETE \
    -H "Authorization: Bearer $GITHUB_TOKEN" \
    "https://api.github.com/repos/$GITHUB_OWNER/$GITHUB_REPO/git/refs/heads/$BRANCH" > /dev/null || true
  echo "    $BRANCH removed (if it existed)"
fi

echo
echo "Ready. Production is failing, nothing is approved, and the timeline is empty."
