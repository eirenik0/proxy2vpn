.PHONY: help test changelog changelog-draft release

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
