.PHONY: help setup start stop test demo reset clean

help:
	@echo "PatchPilot"
	@echo "  make setup   install deps and configure the TrueForge harness"
	@echo "  make start   run harness, simulator, MCP servers, api, web"
	@echo "  make stop    stop everything"
	@echo "  make test    run the full test suite"
	@echo "  make demo    reset to a clean incident and start the demo"
	@echo "  make reset   reset simulator + repo + session state"

setup:
	@bash scripts/setup.sh

start:
	@bash scripts/run-demo.sh

stop:
	@bash scripts/stop.sh

test:
	@bash scripts/test.sh

reset:
	@bash scripts/reset-demo.sh

demo: reset start
