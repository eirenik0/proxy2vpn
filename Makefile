.PHONY: help lint-main lint-tests lint fmt-main fmt-tests fmt check all clean test

SHELL := /bin/bash

# Colors
GREEN  := $(shell tput -Txterm setaf 2)
YELLOW := $(shell tput -Txterm setaf 3)
WHITE  := $(shell tput -Txterm setaf 7)
RESET  := $(shell tput -Txterm sgr0)

# Shell script files
SHELL_FILES := $(wildcard *.sh) $(wildcard scripts/*.sh)
TEST_FILES := $(wildcard tests/integration_*.sh) $(wildcard tests/setup_test_env.sh) $(wildcard tests/run_integration_tests.sh)

# ShellCheck options
# SC2034: Unused variables (sometimes variables are for documentation)
# SC2086: Word splitting issues (sometimes splitting is desired)
# SC2155: Declare and assign separately (common pattern in scripts)
# SC2317: Unreachable code/declarations (function declarations)
# SC2310: Function invoked in condition (info)
# SC2312: Consider invoking command separately (info)
SHELLCHECK_EXCLUDES := SC2034,SC2086,SC2155,SC2317,SC2310,SC2312
TEST_SHELLCHECK_EXCLUDES := $(SHELLCHECK_EXCLUDES)

help: ## Show this help
	@echo ''
	@echo 'Usage:'
	@echo '  ${YELLOW}make${RESET} ${GREEN}<target>${RESET}'
	@echo ''
	@echo 'Targets:'

all: lint ## Run all checks and formatting
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  ${YELLOW}%-15s${RESET} ${GREEN}%s${RESET}\n", $$1, $$2}' $(MAKEFILE_LIST)

check: ## Verify that dependencies are installed
	@command -v shellcheck >/dev/null 2>&1 || { echo >&2 "${YELLOW}shellcheck${RESET} is required but not installed. Install with: ${GREEN}brew install shellcheck${RESET}"; exit 1; }
	@command -v shfmt >/dev/null 2>&1 || { echo >&2 "${YELLOW}shfmt${RESET} is required but not installed. Install with: ${GREEN}brew install shfmt${RESET}"; exit 1; }

lint-main: check ## Lint main shell scripts with shellcheck
	@echo "${YELLOW}Running shellcheck on main scripts...${RESET}"
	@shellcheck --shell=bash --enable=all --exclude=$(SHELLCHECK_EXCLUDES) -x $(SHELL_FILES)
	@echo "${GREEN}✓ Main scripts shellcheck passed${RESET}"

lint-tests: check ## Lint test scripts with shellcheck
	@echo "${YELLOW}Running shellcheck on test scripts...${RESET}"
	@shellcheck --shell=bash --enable=all --exclude=$(TEST_SHELLCHECK_EXCLUDES) -x $(TEST_FILES)
	@echo "${GREEN}✓ Test scripts shellcheck passed${RESET}"

lint: lint-main lint-tests ## Lint all shell scripts with shellcheck
	@echo "${GREEN}✓ All shellcheck checks passed${RESET}"

fmt-main: check ## Format main shell scripts
	@echo "${YELLOW}Formatting main scripts...${RESET}"
	@shfmt -w -i 2 -ci -bn -kp $(SHELL_FILES)
	@echo "${GREEN}✓ Main scripts formatting completed${RESET}"

fmt-tests: check ## Format test shell scripts
	@echo "${YELLOW}Formatting test scripts...${RESET}"
	@shfmt -w -i 2 -ci -bn -kp $(TEST_FILES)
	@echo "${GREEN}✓ Test scripts formatting completed${RESET}"

fmt: fmt-main fmt-tests ## Format all shell scripts
	@echo "${GREEN}✓ All formatting completed${RESET}"

fmt-check-main: check ## Check main shell script formatting
	@echo "${YELLOW}Checking main script formatting...${RESET}"
	@shfmt -d -i 2 -ci -bn -kp $(SHELL_FILES) && echo "${GREEN}✓ Main script formatting is correct${RESET}" || echo "${YELLOW}× Main script formatting issues found. Run 'make fmt-main' to fix.${RESET}"

fmt-check-tests: check ## Check test shell script formatting
	@echo "${YELLOW}Checking test script formatting...${RESET}"
	@shfmt -d -i 2 -ci -bn -kp $(TEST_FILES) && echo "${GREEN}✓ Test script formatting is correct${RESET}" || echo "${YELLOW}× Test script formatting issues found. Run 'make fmt-tests' to fix.${RESET}"

fmt-check: fmt-check-main fmt-check-tests ## Check all shell script formatting
	@echo "${GREEN}✓ All formatting checks completed${RESET}"

test-prep: ## Prepare test environment
	@echo "${YELLOW}Preparing test environment...${RESET}"
	@chmod +x $(TEST_FILES)
	@echo "${GREEN}✓ Test environment prepared${RESET}"

test: test-prep ## Run test suite
	@echo "${YELLOW}Running integration tests...${RESET}"
	@./tests/run_integration_tests.sh && echo "${GREEN}✓ All tests passed${RESET}" || { echo "${YELLOW}× Some tests failed${RESET}"; exit 1; }

test-lint: lint-tests test-prep ## Lint test scripts and run tests
	@echo "${YELLOW}Running linted tests...${RESET}"
	@./tests/run_integration_tests.sh && echo "${GREEN}✓ All linted tests passed${RESET}" || { echo "${YELLOW}× Some tests failed${RESET}"; exit 1; }

all: lint fmt test ## Run all checks, format code, and tests

clean: ## Clean up temporary files
	@echo "${YELLOW}Cleaning up...${RESET}"
	@find . -name "*.bak" -type f -delete
	@find . -name "*.tmp" -type f -delete
	@rm -f env.test
	@echo "${GREEN}✓ Cleanup completed${RESET}"
