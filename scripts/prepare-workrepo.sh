#!/usr/bin/env bash
# Create the agent's working clone.
#
# PatchPilot never operates on your checkout. It works in a dedicated clone under
# .workrepo/ so that a confused agent cannot disturb work in progress, and so the
# demo can be reset by deleting a directory.
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
[ -f .env ] && set -a && . ./.env && set +a

OWNER="${GITHUB_OWNER:?GITHUB_OWNER must be set in .env}"
REPO="${GITHUB_REPO:?GITHUB_REPO must be set in .env}"
BRANCH="${PATCHPILOT_BASE_BRANCH:-${GITHUB_DEFAULT_BRANCH:-main}}"
TARGET="${WORK_REPO_PATH:-$PWD/.workrepo}"

# This script deletes TARGET. A misconfigured WORK_REPO_PATH pointing at a home
# directory or the project root would therefore destroy unrelated work, so the
# path must look like a dedicated clone before anything is removed: either it
# does not exist yet, or it is a git repository we previously created.
if [ -e "$TARGET" ]; then
  case "$(basename "$TARGET")" in
    .workrepo|workrepo|*-workrepo) ;;
    *)
      echo "error: refusing to delete '$TARGET'." >&2
      echo "       WORK_REPO_PATH must be a dedicated clone directory named" >&2
      echo "       .workrepo, workrepo, or *-workrepo." >&2
      exit 1
      ;;
  esac
  if [ ! -d "$TARGET/.git" ]; then
    echo "error: refusing to delete '$TARGET': it is not a git clone." >&2
    exit 1
  fi
fi

echo "==> preparing working clone at $TARGET (branch $BRANCH)"
rm -rf "$TARGET"

# Credentials go through askpass so the token never lands in the remote URL or
# in .git/config, where a later `git remote -v` would print it.
ASKPASS="$(mktemp)"
cat > "$ASKPASS" <<'EOS'
#!/bin/sh
case "$1" in
  Username*) printf '%s' "x-access-token" ;;
  *)         printf '%s' "$GITHUB_TOKEN" ;;
esac
EOS
chmod 700 "$ASKPASS"
trap 'rm -f "$ASKPASS"' EXIT

GIT_ASKPASS="$ASKPASS" GIT_TERMINAL_PROMPT=0 \
  git clone --quiet --branch "$BRANCH" "https://github.com/$OWNER/$REPO.git" "$TARGET"

git -C "$TARGET" config user.email "${GIT_AUTHOR_EMAIL:-patchpilot@users.noreply.github.com}"
git -C "$TARGET" config user.name "${GIT_AUTHOR_NAME:-PatchPilot}"

echo "==> ready: $(git -C "$TARGET" rev-parse --short HEAD) on $BRANCH"
