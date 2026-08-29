#!/usr/bin/env bash
# Create the Python environment and install PatchPilot's dependencies.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is required. Install it from https://docs.astral.sh/uv/" >&2
  exit 1
fi

echo "==> creating .venv (Python 3.12)"
uv venv --python 3.12

echo "==> installing dependencies"
uv pip install -e ".[dev]"

if [ ! -f .env ]; then
  echo "==> creating .env from .env.example"
  cp .env.example .env
  echo "    edit .env to add your GITHUB_TOKEN before using the GitHub tools"
fi

echo "==> setup complete"
