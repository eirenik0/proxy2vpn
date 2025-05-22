#!/bin/bash
# integration_security.sh - Security tests for proxy2vpn
#
# Tests credential handling, input validation, and file permissions

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test directory
TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROXY2VPN="${TEST_DIR}/../proxy2vpn.sh"

# Test results
tests_passed=0
tests_failed=0

# Helper functions
run_test() {
    local test_name="$1"
    local test_function="$2"
    
    echo -e "${YELLOW}Running test: ${test_name}...${NC}"
    
    if "${test_function}"; then
        echo -e "${GREEN}✓ ${test_name} passed${NC}"
        tests_passed=$((tests_passed + 1))
    else
        echo -e "${RED}✗ ${test_name} failed${NC}"
        tests_failed=$((tests_failed + 1))
    fi
}

# Test: Profile file permissions
test_profile_permissions() {
    local profile_name="sectest_profile"
    
    # Create a profile
    "${PROXY2VPN}" create-profile "${profile_name}" testuser testpass >/dev/null 2>&1
    
    # Check file permissions (should be 600)
    local profile_file="${TEST_DIR}/../profiles/${profile_name}.env"
    local perms
    # macOS uses different stat format
    if [[ "$(uname)" == "Darwin" ]]; then
        perms=$(stat -f "%Lp" "${profile_file}" 2>/dev/null)
    else
        perms=$(stat -c "%a" "${profile_file}" 2>/dev/null)
    fi
    
    # Clean up
    rm -f "${profile_file}"
    
    [[ "${perms}" == "600" ]]
}

# Test: Invalid container name validation
test_invalid_container_name() {
    # Test with special characters
    if "${PROXY2VPN}" create-from-profile "test@container" 8888 testprofile >/dev/null 2>&1; then
        return 1
    fi
    
    # Test with spaces
    if "${PROXY2VPN}" create-from-profile "test container" 8889 testprofile >/dev/null 2>&1; then
        return 1
    fi
    
    # Test with path traversal
    if "${PROXY2VPN}" create-from-profile "../test" 8890 testprofile >/dev/null 2>&1; then
        return 1
    fi
    
    return 0
}

# Test: Invalid port validation
test_invalid_port() {
    # Test with port out of range
    if "${PROXY2VPN}" create-from-profile "testport1" 0 testprofile >/dev/null 2>&1; then
        return 1
    fi
    
    if "${PROXY2VPN}" create-from-profile "testport2" 99999 testprofile >/dev/null 2>&1; then
        return 1
    fi
    
    # Test with non-numeric port
    if "${PROXY2VPN}" create-from-profile "testport3" "abc" testprofile >/dev/null 2>&1; then
        return 1
    fi
    
    return 0
}

# Test: Invalid profile name validation
test_invalid_profile_name() {
    # Test with special characters
    if "${PROXY2VPN}" create-profile "test@profile" user pass >/dev/null 2>&1; then
        return 1
    fi
    
    # Test with path traversal
    if "${PROXY2VPN}" create-profile "../test" user pass >/dev/null 2>&1; then
        return 1
    fi
    
    return 0
}

# Test: Port conflict detection
test_port_conflict() {
    # Create a test profile first
    "${PROXY2VPN}" create-profile "porttest" testuser testpass >/dev/null 2>&1
    
    # Create a container on port 8891
    "${PROXY2VPN}" create-from-profile "porttest1" 8891 porttest >/dev/null 2>&1
    
    # Try to create another container on the same port
    local result=0
    if "${PROXY2VPN}" create-from-profile "porttest2" 8891 porttest >/dev/null 2>&1; then
        result=1
    fi
    
    # Clean up
    docker stop "porttest1" >/dev/null 2>&1 || true
    docker rm "porttest1" >/dev/null 2>&1 || true
    rm -f "${TEST_DIR}/profiles/porttest.env"
    
    return ${result}
}

# Test: Temp file security
test_temp_file_security() {
    # Create a test profile and container
    "${PROXY2VPN}" create-profile "temptest" testuser testpass >/dev/null 2>&1
    "${PROXY2VPN}" create-from-profile "temptest1" 8892 temptest >/dev/null 2>&1
    
    # Create a preset (which uses temp files)
    "${PROXY2VPN}" create-preset "temptest1" "temptestpreset" >/dev/null 2>&1
    
    # Check that no predictable temp files exist
    local result=0
    shopt -s nullglob
    local temp_files=(/tmp/preset_env_*.json)
    if [[ ${#temp_files[@]} -gt 0 ]]; then
        result=1
    fi
    shopt -u nullglob
    
    # Clean up
    docker stop "temptest1" >/dev/null 2>&1 || true
    docker rm "temptest1" >/dev/null 2>&1 || true
    rm -f "${TEST_DIR}/profiles/temptest.env"
    rm -f "${TEST_DIR}/presets/presets.json"
    
    return ${result}
}

# Run all tests
echo "🔒 Running security integration tests..."

run_test "Profile file permissions" test_profile_permissions
run_test "Invalid container name validation" test_invalid_container_name
run_test "Invalid port validation" test_invalid_port
run_test "Invalid profile name validation" test_invalid_profile_name
run_test "Port conflict detection" test_port_conflict
run_test "Temp file security" test_temp_file_security

# Summary
echo ""
echo "📊 Security Test Results:"
echo "  ✓ Passed: ${tests_passed}"
echo "  ✗ Failed: ${tests_failed}"

exit "${tests_failed}"