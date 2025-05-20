.PHONY: help lint fmt check all clean

SHELL := /bin/bash

# Colors
GREEN  := $(shell tput -Txterm setaf 2)
YELLOW := $(shell tput -Txterm setaf 3)
WHITE  := $(shell tput -Txterm setaf 7)
RESET  := $(shell tput -Txterm sgr0)

# Shell script files
SHELL_FILES := $(wildcard *.sh)

help: ## Show this help
	@echo ''
	@echo 'Usage:'
	@echo '  ${YELLOW}make${RESET} ${GREEN}<target>${RESET}'
	@echo ''
	@echo 'Targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  ${YELLOW}%-15s${RESET} ${GREEN}%s${RESET}\n", $$1, $$2}' $(MAKEFILE_LIST)

check: ## Verify that dependencies are installed
	@command -v shellcheck >/dev/null 2>&1 || { echo >&2 "${YELLOW}shellcheck${RESET} is required but not installed. Install with: ${GREEN}brew install shellcheck${RESET}"; exit 1; }
	@command -v shfmt >/dev/null 2>&1 || { echo >&2 "${YELLOW}shfmt${RESET} is required but not installed. Install with: ${GREEN}brew install shfmt${RESET}"; exit 1; }

lint: check ## Lint shell scripts with shellcheck
	@echo "${YELLOW}Running shellcheck...${RESET}"
	@shellcheck --shell=bash --enable=all --exclude=SC2317,SC2155,SC2310,SC2250,SC2178,SC2248,SC2312,SC2031,SC2034,SC2249,SC2001,SC2030,SC2012,SC2128 $(SHELL_FILES)
	@echo "${GREEN}✓ Shellcheck passed${RESET}"

fmt-check: check ## Check shell script formatting
	@echo "${YELLOW}Checking shell script formatting...${RESET}"
	@shfmt -d -i 2 -ci -bn -kp $(SHELL_FILES) && echo "${GREEN}✓ Formatting is correct${RESET}" || echo "${YELLOW}× Formatting issues found. Run 'make fmt' to fix.${RESET}"

fmt: check ## Format shell scripts
	@echo "${YELLOW}Formatting shell scripts...${RESET}"
	@shfmt -w -i 2 -ci -bn -kp $(SHELL_FILES)
	@echo "${GREEN}✓ Formatting completed${RESET}"

all: lint fmt ## Run all checks and format code

clean: ## Clean up temporary files
	@echo "${YELLOW}Cleaning up...${RESET}"
	@find . -name "*.bak" -type f -delete
	@find . -name "*.tmp" -type f -delete
	@echo "${GREEN}✓ Cleanup completed${RESET}"