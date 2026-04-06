.PHONY: help test changelog-check changelog changelog-draft release lint fmt fmt-check clean all

UV_PATH = PATH="$$PATH:$(HOME)/.local/bin"

help: ## Show available targets
		@echo "Targets:"
		@echo "  test              Run unit tests"
		@echo "  changelog-check   Validate Towncrier news fragments"
		@echo "  changelog         Build changelog for a release (requires VERSION)"
		@echo "  changelog-draft   Preview next changelog"
		@echo "  release           Bump version, build changelog and tag"
		@echo "  lint              Run Python linting (ruff + ty)"
		@echo "  fmt               Format Python code with ruff"
		@echo "  fmt-check         Check Python formatting without modifying files"
		@echo "  clean             Clean up temporary files and caches"
		@echo "  all               Run all checks (format check, lint, test)"

test:
	$(UV_PATH) uv run --with pytest,pytest-xdist pytest -n auto

changelog-check:
	$(UV_PATH) uv run --with towncrier towncrier check

changelog:
	@[ -n "$(VERSION)" ] || (echo "VERSION is required" && exit 1)
	$(UV_PATH) uv run --with towncrier towncrier build --yes --version $(VERSION)

changelog-draft:
	$(UV_PATH) uv run --with towncrier towncrier build --draft

release:
	@[ -n "$(VERSION)" ] || (echo "VERSION is required" && exit 1)
	$(UV_PATH) uv run --with towncrier python scripts/bump_version.py $(VERSION)
	$(UV_PATH) uv run --with towncrier towncrier build --yes --version $(VERSION)
	git commit -am "Release $(VERSION)"
	git tag v$(VERSION)

lint: ## Run Python linting (ruff + ty)
	$(UV_PATH) uv run --with ruff ruff check src/ tests/
	$(UV_PATH) uvx ty check

fmt: ## Format Python code with ruff
	$(UV_PATH) uv run --with ruff ruff format src/ tests/

fmt-check: ## Check Python formatting without modifying files
	$(UV_PATH) uv run --with ruff ruff format --check src/ tests/

clean: ## Clean up temporary files and caches
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache
	rm -rf src/*.egg-info
	rm -rf build dist

all: fmt-check lint test ## Run all checks (format check, lint, test)
	@echo "All checks passed!"
