.PHONY: help test

help: ## Show available targets
	@echo "Targets:"
	@echo "  test    Run unit tests"

test:
	uv run --with pytest pytest
