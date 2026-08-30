# PatchPilot. Targets are added as the components they drive land, so every
# target here runs today.
.PHONY: help setup harness services api web stop configure demo reset test lint

help:
	@echo "PatchPilot"
	@echo "  make setup      create the Python environment and install dependencies"
	@echo "  make services   start the simulator and the three MCP servers"
	@echo "  make harness    start the TrueForge harness"
	@echo "  make configure  configure the harness: model, sandbox, connectors, agents"
	@echo "  make stop       stop the simulator and MCP servers"
	@echo "  make api        start the orchestrator API"
	@echo "  make demo       start everything, ready to demonstrate"
	@echo "  make reset      return the demo to its opening scene"
	@echo "  make test       run the test suite"
	@echo "  make lint       run the linter"

services:
	@bash scripts/run-services.sh

harness:
	@bash scripts/run-harness.sh

configure:
	@.venv/bin/python scripts/setup_trueforge.py
	@.venv/bin/python scripts/setup_agents.py

api:
	@bash scripts/run-api.sh

demo:
	@bash scripts/run-demo.sh

reset:
	@bash scripts/reset-demo.sh

stop:
	@bash scripts/stop.sh

setup:
	@bash scripts/setup.sh

test:
	@bash scripts/test.sh

lint:
	@.venv/bin/ruff check .
