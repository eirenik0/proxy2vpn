#!/bin/bash
# run_integration_tests.sh - Main runner for proxy2vpn integration tests
#
# This script runs all integration tests for proxy2vpn, which test complete flows
# rather than individual functions.

set -e

# Script directory for relative paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ROOT_DIR is used by the setup_test_env.sh script
cd "${SCRIPT_DIR}/.." || exit 1
ROOT_DIR="$(pwd)"
export ROOT_DIR
cd "${SCRIPT_DIR}" || exit 1

echo "🚀 Starting proxy2vpn integration tests..."

# Make sure all test scripts are executable
chmod +x "${SCRIPT_DIR}"/integration_*.sh

# Setup test environment first
echo "📋 Setting up test environment..."
"${SCRIPT_DIR}/setup_test_env.sh"

# Track test results
tests_passed=0
tests_failed=0
tests_total=0

# Run all integration test scripts
for test_script in "${SCRIPT_DIR}"/integration_*.sh; do
    if [[ -f "${test_script}" ]]; then
        tests_total=$((tests_total + 1))
        echo ""
        echo "📋 Running integration test: $(basename "${test_script}")..."

        if "${test_script}"; then
            echo "✅ Integration test passed: $(basename "${test_script}")"
            tests_passed=$((tests_passed + 1))
        else
            echo "❌ Integration test failed: $(basename "${test_script}")"
            tests_failed=$((tests_failed + 1))
        fi
    fi
done

echo ""
echo "📊 Integration Test Summary:"
echo "  ↳ Total tests: ${tests_total}"
echo "  ↳ Passed: ${tests_passed}"
echo "  ↳ Failed: ${tests_failed}"

if [[ "${tests_failed}" -eq 0 ]]; then
    echo "✅ All integration tests passed!"
    exit 0
else
    echo "❌ ${tests_failed} integration test(s) failed!"
    exit 1
fi