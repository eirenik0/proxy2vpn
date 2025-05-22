#!/bin/bash

# integration_environment_variables.sh - Test Docker environment variable injection
# This test verifies that environment variables passed to containers match expected values

# Get test environment variables
TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="${TEST_DIR}/../proxy2vpn.sh"
CONTAINER_NAME="env_test_vpn"
PORT="7788"
TEST_PROVIDER="protonvpn"
TEST_LOCATION="AL"
TEST_PROFILE="test"

# Color functions
print_success() { echo -e "\033[0;32m✓ $1\033[0m"; }
print_error() { echo -e "\033[0;31m✗ $1\033[0m"; }
print_info() { echo -e "\033[0;34m→ $1\033[0m"; }

# Test counter
total_tests=6
passed_tests=0

check_result() {
    local test_name="$1"
    local result="$2"

    if [[ ${result} -eq 0 ]]; then
        print_success "${test_name} passed"
        ((passed_tests++))
    else
        print_error "${test_name} failed"
    fi
}

# Cleanup function
cleanup() {
    echo "🧹 Cleaning up test resources..."
    docker rm -f "${CONTAINER_NAME}" "vpn_${CONTAINER_NAME}" >/dev/null 2>&1 || true
}

# Setup cleanup trap
trap cleanup EXIT

# Initial cleanup
cleanup

echo "📋 Starting Environment Variables Integration Test..."

# Test 1: Create container with basic parameters
echo "🧪 Test 1: Creating container with provider and location..."
"${SCRIPT_PATH}" create "${CONTAINER_NAME}" "${PORT}" "${TEST_PROVIDER}" "${TEST_LOCATION}"
creation_result=$?
check_result "Container creation" "${creation_result}"

# Get all environment variables from the container (it has vpn_ prefix)
container_env=$(docker inspect "vpn_${CONTAINER_NAME}" | jq -r '.[0].Config.Env[]' 2>/dev/null || echo "")

# Test 2: Verify VPN_SERVICE_PROVIDER
echo "🧪 Test 2: Verifying VPN_SERVICE_PROVIDER environment variable..."
provider_env=$(echo "${container_env}" | grep "^VPN_SERVICE_PROVIDER=" || echo "")
expected_provider="VPN_SERVICE_PROVIDER=${TEST_PROVIDER}"

if [[ "${provider_env}" == "${expected_provider}" ]]; then
    check_result "VPN_SERVICE_PROVIDER verification" 0
    print_info "Expected: ${expected_provider}"
    print_info "Actual: ${provider_env}"
else
    check_result "VPN_SERVICE_PROVIDER verification" 1
    print_info "Expected: ${expected_provider}"
    print_info "Actual: ${provider_env}"
fi

# Test 3: Verify HTTPPROXY is enabled
echo "🧪 Test 3: Verifying HTTPPROXY environment variable..."

# Check if any HTTPPROXY variable is set to "on" (with possible whitespace)
if echo "${container_env}" | grep -q "HTTPPROXY=on"; then
    check_result "HTTPPROXY verification" 0
    print_info "Expected: HTTPPROXY=on"
    print_info "Actual: Found HTTPPROXY=on among environment variables"
else
    check_result "HTTPPROXY verification" 1
    print_info "Expected: HTTPPROXY=on"
    print_info "Debug: Found these HTTPPROXY variables:"
    echo "${container_env}" | grep HTTPPROXY || echo "None found"
fi

# Test 4: Verify HTTPPROXY_LISTENING_ADDRESS
echo "🧪 Test 4: Verifying HTTPPROXY_LISTENING_ADDRESS environment variable..."
httpproxy_addr_env=$(echo "${container_env}" | grep "^HTTPPROXY_LISTENING_ADDRESS=" || echo "")
expected_httpproxy_addr="HTTPPROXY_LISTENING_ADDRESS=:8888"

if [[ "${httpproxy_addr_env}" == "${expected_httpproxy_addr}" ]]; then
    check_result "HTTPPROXY_LISTENING_ADDRESS verification" 0
    print_info "Expected: ${expected_httpproxy_addr}"
    print_info "Actual: ${httpproxy_addr_env}"
else
    check_result "HTTPPROXY_LISTENING_ADDRESS verification" 1
    print_info "Expected: ${expected_httpproxy_addr}"
    print_info "Actual: ${httpproxy_addr_env}"
fi

# Test 5: Verify location-based environment variables (SERVER_COUNTRIES or SERVER_CITIES)
echo "🧪 Test 5: Verifying location environment variables..."
server_countries_env=$(echo "${container_env}" | grep "^SERVER_COUNTRIES=" || echo "")
server_cities_env=$(echo "${container_env}" | grep "^SERVER_CITIES=" || echo "")

# The location should result in either SERVER_COUNTRIES or SERVER_CITIES being set
if [[ -n "${server_countries_env}" ]] || [[ -n "${server_cities_env}" ]]; then
    check_result "Location environment variables verification" 0
    if [[ -n "${server_countries_env}" ]]; then
        print_info "Found SERVER_COUNTRIES: ${server_countries_env}"
    fi
    if [[ -n "${server_cities_env}" ]]; then
        print_info "Found SERVER_CITIES: ${server_cities_env}"
    fi
else
    check_result "Location environment variables verification" 1
    print_info "No SERVER_COUNTRIES or SERVER_CITIES found"
fi

# Cleanup the container
docker rm -f "vpn_${CONTAINER_NAME}" >/dev/null 2>&1 || true

# Test 6: Create container with profile and verify credentials
echo "🧪 Test 6: Creating container with profile credentials..."
"${SCRIPT_PATH}" create-from-profile "${CONTAINER_NAME}" "${PORT}" "${TEST_PROFILE}" "" "" "${TEST_PROVIDER}"
profile_creation_result=$?

if [[ ${profile_creation_result} -eq 0 ]]; then
    # Get environment variables again (it has vpn_ prefix)
    container_env=$(docker inspect "vpn_${CONTAINER_NAME}" | jq -r '.[0].Config.Env[]' 2>/dev/null || echo "")

    # Check for OpenVPN credentials
    openvpn_user_env=$(echo "${container_env}" | grep "^OPENVPN_USER=" || echo "")
    openvpn_password_env=$(echo "${container_env}" | grep "^OPENVPN_PASSWORD=" || echo "")

    if [[ -n "${openvpn_user_env}" ]] && [[ -n "${openvpn_password_env}" ]]; then
        check_result "Profile credentials verification" 0
        print_info "Found OPENVPN_USER: ${openvpn_user_env}"
        print_info "Found OPENVPN_PASSWORD: [REDACTED]"
    else
        check_result "Profile credentials verification" 1
        print_info "OPENVPN_USER: ${openvpn_user_env}"
        print_info "OPENVPN_PASSWORD: ${openvpn_password_env}"
    fi
else
    check_result "Profile credentials verification" 1
    print_info "Container creation failed, cannot verify credentials"
fi

# Summary
echo ""
echo "📊 Environment Variables Integration Test Summary:"
echo "  ↳ Total tests: ${total_tests}"
echo "  ↳ Passed: ${passed_tests}"
echo "  ↳ Failed: $((total_tests - passed_tests))"

if [[ ${passed_tests} -eq ${total_tests} ]]; then
    print_success "All environment variable tests passed!"
else
    print_error "$((total_tests - passed_tests)) environment variable tests failed!"
    exit 1
fi
