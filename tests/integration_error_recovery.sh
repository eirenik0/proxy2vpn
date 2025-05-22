#!/bin/bash
# integration_error_recovery.sh - Error recovery tests for proxy2vpn
#
# Tests error handling and recovery scenarios

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

# Test: Missing profile error handling
test_missing_profile() {
    # Try to create container with non-existent profile
    if "${PROXY2VPN}" create-from-profile "errortest1" 8893 "nonexistentprofile" >/dev/null 2>&1; then
        return 1
    fi
    
    return 0
}

# Test: Invalid provider error handling
test_invalid_provider() {
    # Create a test profile
    "${PROXY2VPN}" create-profile "errortest" testuser testpass >/dev/null 2>&1
    
    # Try to create container with invalid provider (should fail due to validation)
    local result=0
    if "${PROXY2VPN}" create-from-profile "errortest2" 8894 "errortest" "" "" "invalidprovider" >/dev/null 2>&1; then
        result=1  # Test failed - container was created with invalid provider
    else
        result=0  # Test passed - container creation failed as expected
    fi
    
    # Clean up
    rm -f "${TEST_DIR}/../profiles/errortest.env"
    
    return ${result}
}

# Test: Container cleanup on creation failure
test_container_cleanup_on_failure() {
    # Test that proxy2vpn properly handles container name conflicts
    
    # Create profile
    "${PROXY2VPN}" create-profile "cleanuptest" testuser testpass >/dev/null 2>&1
    
    # Create a VPN container first
    "${PROXY2VPN}" create-from-profile "cleanup_test" 8895 "cleanuptest" >/dev/null 2>&1
    
    # Try to create another VPN container with the same name (should fail)
    local result=0
    if "${PROXY2VPN}" create-from-profile "cleanup_test" 8896 "cleanuptest" >/dev/null 2>&1; then
        result=1  # Test failed - duplicate container was created
    else
        result=0  # Test passed - duplicate container creation was prevented
    fi
    
    # Verify only one container exists
    local container_count=$(docker ps -a --filter "name=vpn_cleanup_test" --format "{{.Names}}" | wc -l)
    if [[ ${container_count} -ne 1 ]]; then
        result=1  # Test failed - wrong number of containers
    fi
    
    # Clean up
    docker stop "vpn_cleanup_test" >/dev/null 2>&1 || true
    docker rm "vpn_cleanup_test" >/dev/null 2>&1 || true
    rm -f "${TEST_DIR}/../profiles/cleanuptest.env"
    
    return ${result}
}

# Test: Update non-existent container
test_update_nonexistent() {
    if "${PROXY2VPN}" update "nonexistentcontainer" "HTTPPROXY_USER" "testuser" >/dev/null 2>&1; then
        return 1
    fi
    
    return 0
}

# Test: Delete non-existent container
test_delete_nonexistent() {
    if "${PROXY2VPN}" delete "nonexistentcontainer" >/dev/null 2>&1; then
        return 1
    fi
    
    return 0
}

# Test: Apply preset to existing container
test_apply_preset_conflict() {
    # Create profile and container
    "${PROXY2VPN}" create-profile "conflicttest" testuser testpass >/dev/null 2>&1
    "${PROXY2VPN}" create-from-profile "conflicttest1" 8896 "conflicttest" >/dev/null 2>&1
    
    # Create preset
    "${PROXY2VPN}" create-preset "conflicttest1" "conflictpreset" >/dev/null 2>&1
    
    # Try to apply preset with same name (should fail)
    local result=0
    if "${PROXY2VPN}" apply-preset "conflictpreset" "conflicttest1" >/dev/null 2>&1; then
        result=1
    fi
    
    # Clean up
    docker stop "conflicttest1" >/dev/null 2>&1 || true
    docker rm "conflicttest1" >/dev/null 2>&1 || true
    rm -f "${TEST_DIR}/profiles/conflicttest.env"
    rm -f "${TEST_DIR}/presets/presets.json"
    
    return ${result}
}

# Test: Batch operation with invalid data
test_batch_invalid_data() {
    # Create an invalid batch file
    local batch_file="${TEST_DIR}/invalid_batch.json"
    cat > "${batch_file}" <<EOF
{
    "invalid_container": {
        "port": "not_a_number",
        "user_profile": "nonexistent"
    }
}
EOF
    
    # Try to create batch (should fail gracefully)
    local result=0
    if "${PROXY2VPN}" create-batch "${batch_file}" >/dev/null 2>&1; then
        result=1
    fi
    
    # Clean up
    rm -f "${batch_file}"
    
    return ${result}
}

# Run all tests
echo "🔧 Running error recovery integration tests..."

run_test "Missing profile error handling" test_missing_profile
run_test "Invalid provider error handling" test_invalid_provider
run_test "Container cleanup on failure" test_container_cleanup_on_failure
run_test "Update non-existent container" test_update_nonexistent
run_test "Delete non-existent container" test_delete_nonexistent
run_test "Apply preset conflict" test_apply_preset_conflict
run_test "Batch operation with invalid data" test_batch_invalid_data

# Summary
echo ""
echo "📊 Error Recovery Test Results:"
echo "  ✓ Passed: ${tests_passed}"
echo "  ✗ Failed: ${tests_failed}"

exit "${tests_failed}"