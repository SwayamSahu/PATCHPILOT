# PatchPilot. Targets are added as the components they drive land, so every
# target here runs today.
.PHONY: help setup test lint

help:
	@echo "PatchPilot"
	@echo "  make setup   create the Python environment and install dependencies"
	@echo "  make test    run the test suite"
	@echo "  make lint    run the linter"

setup:
	@bash scripts/setup.sh

test:
	@bash scripts/test.sh

lint:
	@.venv/bin/ruff check .
