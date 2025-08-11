.PHONY: help test changelog changelog-draft release lint format format-check integration-test clean all

help: ## Show available targets
@echo "Targets:"
@echo "  test              Run unit tests"
@echo "  changelog         Build changelog for a release (requires VERSION)"
@echo "  changelog-draft   Preview next changelog"
@echo "  release           Bump version, build changelog and tag"

test:
  uv run --with pytest pytest

changelog:
  @[ -n "$(VERSION)" ] || (echo "VERSION is required" && exit 1)
  uv run --with towncrier towncrier build --yes --version $(VERSION)

changelog-draft:
  uv run --with towncrier towncrier build --draft

release:
  @[ -n "$(VERSION)" ] || (echo "VERSION is required" && exit 1)
  uv run --with towncrier python scripts/bump_version.py $(VERSION)
  uv run --with towncrier towncrier build --yes --version $(VERSION)
  git commit -am "Release $(VERSION)"
  git tag v$(VERSION)

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
