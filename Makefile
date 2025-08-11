.PHONY: help install dev test lint format format-check integration-test clean all

help: ## Show available targets
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

test: ## Run Python unit tests
	uv run --with pytest pytest

lint: ## Run Python linting (ruff)
	uv run --with ruff ruff check src/ tests/

fmt: ## Format Python code with ruff
	uv run --with ruff ruff format src/ tests/

fmt-check: ## Check Python formatting without modifying files
	uv run --with ruff ruff format --check src/ tests/

integration-test: ## Run bash integration tests
	@echo "Running integration tests..."
	@cd tests && ./run_integration_tests.sh

clean: ## Clean up temporary files and caches
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache
	rm -rf src/*.egg-info
	rm -rf build dist

all: format lint test integration-test ## Run all checks (format, lint, test, integration)
	@echo "All checks passed!"
