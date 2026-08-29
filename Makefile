# PatchPilot. Targets are added as the components they drive land, so every
# target here runs today.
.PHONY: help setup harness services stop configure test lint

help:
	@echo "PatchPilot"
	@echo "  make setup      create the Python environment and install dependencies"
	@echo "  make services   start the simulator and the three MCP servers"
	@echo "  make harness    start the TrueForge harness"
	@echo "  make configure  configure the harness: model, sandbox, connectors, agents"
	@echo "  make stop       stop the simulator and MCP servers"
	@echo "  make test       run the test suite"
	@echo "  make lint       run the linter"

services:
	@bash scripts/run-services.sh

harness:
	@bash scripts/run-harness.sh

configure:
	@.venv/bin/python scripts/setup_trueforge.py
	@.venv/bin/python scripts/setup_agents.py

stop:
	@bash scripts/stop.sh

setup:
	@bash scripts/setup.sh

test:
	@bash scripts/test.sh

lint:
	@.venv/bin/ruff check .
