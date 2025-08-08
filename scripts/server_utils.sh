#!/bin/bash
#
# server_utils.sh - Utility functions for VPN server list management
# This file contains functions to directly access server data from the main JSON file
# without creating separate cache files for countries, cities, and servers.

# ======================================================
# Configuration Variables
# ======================================================

# Import these variables from the main script if they're not defined
# This prevents the "referenced but not assigned" shellcheck warnings
: "${SERVERS_CACHE_FILE:=${HOME}/.cache/proxy2vpn/gluetun_servers.json}"
: "${CACHE_TTL:=604800}"  # Default to 7 days in seconds
: "${GLUETUN_SERVERS_URL:=https://raw.githubusercontent.com/qdm12/gluetun/master/internal/storage/servers.json}"
: "${CONFIG_FILE:=${SCRIPT_DIR}/config.json}"

# ======================================================
# Server List Management Functions
# ======================================================

# Fetch the server list from upstream
fetch_server_list() {
    print_info "Fetching VPN server list from gluetun..."

    # Check if the cache exists and is still valid
    if [[ -f "${SERVERS_CACHE_FILE}" ]]; then
        local file_age=$(($(date +%s) - $(date -r "${SERVERS_CACHE_FILE}" +%s)))
        if [[ ${file_age} -lt ${CACHE_TTL} ]]; then
            print_info "Using cached server list (age: $((file_age / 60 / 60)) hours)"
            return 0
        else
            print_info "Cached server list is outdated, refreshing..."
        fi
    else
        print_info "No cached server list found, downloading..."
    fi

    # Ensure the cache directory exists
    mkdir -p "$(dirname "${SERVERS_CACHE_FILE}")"

    # Fetch server list from GitHub
    if ! curl -s -o "${SERVERS_CACHE_FILE}" "${GLUETUN_SERVERS_URL}"; then
        print_error "Failed to download server list from ${GLUETUN_SERVERS_URL}"
        return 1
    fi

    # Validate that the file is valid JSON
    if ! jq empty "${SERVERS_CACHE_FILE}" >/dev/null 2>&1; then
        print_error "Downloaded server list is not valid JSON"
        rm -f "${SERVERS_CACHE_FILE}"
        return 1
    fi

    print_success "Server list fetched and cached successfully"
    return 0
}

# Normalize provider name to match the format in server list
normalize_provider_name() {
    local provider="${1}"

    # Convert to lowercase for case-insensitive matching
    provider=$(echo "${provider}" | tr '[:upper:]' '[:lower:]')

    print_info "Normalizing provider name: ${provider}"

    case "${provider}" in
        "private internet access"|"private"|"privacy"|"pia")
            echo "private internet access"
            ;;
        "perfect"|"perfect privacy")
            echo "perfect privacy"
            ;;
        "vpnunlimited"|"vpn unlimited")
            echo "vpn unlimited"
            ;;
        # Add other provider aliases as needed
        *)
            # Debug direct check
            local jq_provider_check="jq -r --arg provider \"${provider}\" 'if .[\$provider] then \"Provider exists\" else \"Provider not found\" end' \"${SERVERS_CACHE_FILE}\""
            print_info "Provider check: $(eval "${jq_provider_check}")"

            # Get all provider keys for debugging
            local provider_keys=$(jq -r 'keys | join(", ")' "${SERVERS_CACHE_FILE}")
            print_info "Available provider keys: ${provider_keys}"

            echo "${provider}"
            ;;
    esac
}

# Check if a provider exists in the server list
provider_exists() {
    local provider="${1}"
    local normalized_provider="${provider}"

    print_info "Checking if provider exists: ${provider}"

    # Ensure we have the server list
    if ! [[ -f "${SERVERS_CACHE_FILE}" ]]; then
        fetch_server_list || return 1
    fi

    # Check if the provider key exists in the cache file
    local provider_exists=$(jq -r --arg provider "${normalized_provider}" 'if .[$provider] then "true" else "false" end' "${SERVERS_CACHE_FILE}")

    print_info "Provider ${normalized_provider} exists in cache: ${provider_exists}"

    if [[ "${provider_exists}" == "true" ]]; then
        return 0
    else
        print_error "Provider ${normalized_provider} not found in server list"
        # List available providers for debugging
        local available_providers=$(jq -r 'keys | join(", ")' "${SERVERS_CACHE_FILE}")
        print_info "Available providers: ${available_providers}"
        return 1
    fi
}

# Get list of all available providers
get_all_providers() {
    # Ensure we have the server list
    if ! [[ -f "${SERVERS_CACHE_FILE}" ]]; then
        fetch_server_list || return 1
    fi

    jq -r 'keys | .[] | select(. != "version")' "${SERVERS_CACHE_FILE}" | sort
}

# Validate if a provider is supported
validate_provider() {
    local provider="$1"


    # Ensure we have the server list
    if ! [[ -f "${SERVERS_CACHE_FILE}" ]]; then
        fetch_server_list || return 1
    fi

    # Get the actual provider name from the JSON (case-sensitive)
    local provider_lower=$(echo "${provider}" | tr '[:upper:]' '[:lower:]')

    # Get all providers and check for a match (case-insensitive)
    local valid_providers=()
    while IFS= read -r line; do
        valid_providers+=("${line}")
    done < <(jq -r 'keys | .[] | select(. != "version")' "${SERVERS_CACHE_FILE}" | tr '[:upper:]' '[:lower:]' | sort)

    # Check if provider is in the list
    for valid_provider in "${valid_providers[@]}"; do
        if [[ "${provider_lower}" == "${valid_provider}" ]]; then
            return 0
        fi
    done

    # Get the proper case provider names for the error message
    local display_providers=()
    while IFS= read -r line; do
        display_providers+=("${line}")
    done < <(jq -r 'keys | .[] | select(. != "version")' "${SERVERS_CACHE_FILE}" | sort)

    print_error "Invalid VPN provider: ${provider}"
    print_info "Valid providers: ${display_providers[*]}"
    return 1
}

# Get list of countries for a provider
get_countries() {
    local provider="${1}"
    local normalized_provider="$(normalize_provider_name "${provider}")"

    # Ensure we have the server list
    if ! [[ -f "${SERVERS_CACHE_FILE}" ]]; then
        fetch_server_list || return 1
    fi

    # Check if provider exists
    if ! provider_exists "${normalized_provider}"; then
        print_error "Provider ${provider} not found in server list"
        return 1
    fi

    # Extract unique countries with their codes
    jq -r --arg provider "${normalized_provider}" '
        .[$provider].servers |
        group_by(.country) |
        map({
            country: (.[0].country // "Unknown"),
            code: (.[0].country_code // .[0].region //
                  (if .[0].country then (.[0].country | split(" ") | .[0][0:2] | ascii_upcase) else "??" end))
        }) |
        unique_by(.country) |
        sort_by(.country) |
        .[] |
        "\(.country)|\(.code)"
    ' "${SERVERS_CACHE_FILE}"
}

# Get list of cities for a provider in a specific country
get_cities() {
    local provider="${1}"
    local country="${2}"
    local normalized_provider="$(normalize_provider_name "${provider}")"

    # Ensure we have the server list
    if ! [[ -f "${SERVERS_CACHE_FILE}" ]]; then
        fetch_server_list || return 1
    fi

    # Check if provider exists
    if ! provider_exists "${normalized_provider}"; then
        print_error "Provider ${provider} not found in server list"
        return 1
    fi

    # Extract cities for the specified country
    jq -r --arg provider "${normalized_provider}" --arg country "${country}" '
        .[$provider].servers |
        map(select(.country == $country or .country_code == $country or .region == $country)) |
        group_by(.city) |
        map(.[0].city) |
        select(. != null) |
        sort |
        unique
    ' "${SERVERS_CACHE_FILE}"
}

# Get list of servers for a provider, optionally filtered by country and city
get_servers() {
    local provider="${1}"
    local country="${2:-}"
    local city="${3:-}"

    local normalized_provider="$(normalize_provider_name "${provider}")"
    print_info "UTILS: get_servers called with Provider: ${provider}, Country: ${country}, City: ${city}"

    # Ensure we have the server list
    if ! [[ -f "${SERVERS_CACHE_FILE}" ]]; then
        fetch_server_list || return 1
    fi

    # Extract the servers to a temporary file
    local tmp_servers=$(mktemp)
    jq -r --arg provider "${normalized_provider}" '.[$provider].servers // []' "${SERVERS_CACHE_FILE}" > "${tmp_servers}"

    # Check if we got any servers
    if ! jq -e '.[0]' "${tmp_servers}" >/dev/null 2>&1; then
        print_error "No servers found for provider: ${provider}"
        rm -f "${tmp_servers}"
        return 1
    fi

    # Count servers
    local count=$(jq '. | length' "${tmp_servers}")
    print_info "UTILS: Found ${count} servers for ${normalized_provider}"

    # Now create a new array with just the fields we need
    local tmp_formatted=$(mktemp)
    jq '[.[] | {
        hostname: (.hostname // .server_name // null),
        country: (.country // "Unknown"),
        country_code: (.country_code // .region // "??"),
        city: (.city // null),
        type: (.vpn // "openvpn")
    }] | map(select(.hostname != null))' "${tmp_servers}" > "${tmp_formatted}"

    rm -f "${tmp_servers}"

    # Apply country filter if specified
    if [[ -n "${country}" ]]; then
        local tmp_country=$(mktemp)
        jq --arg country "${country}" '[.[] | select(.country == $country or .country_code == $country)]' "${tmp_formatted}" > "${tmp_country}"
        mv "${tmp_country}" "${tmp_formatted}"

        count=$(jq '. | length' "${tmp_formatted}")
        print_info "UTILS: After country filter: ${count} servers"
    fi

    # Apply city filter if specified
    if [[ -n "${city}" ]]; then
        local tmp_city=$(mktemp)
        jq --arg city "${city}" '[.[] | select(.city == $city)]' "${tmp_formatted}" > "${tmp_city}"
        mv "${tmp_city}" "${tmp_formatted}"

        count=$(jq '. | length' "${tmp_formatted}")
        print_info "UTILS: After city filter: ${count} servers"
    fi

    # Return the filtered and formatted servers
    cat "${tmp_formatted}"
    rm -f "${tmp_formatted}"

    print_info "UTILS: Successfully processed servers"
}

# Get the best server for a provider in a specified country and city
get_best_server() {
    local provider="${1}"
    local country="${2:-}"
    local city="${3:-}"

    # Get all matching servers
    local servers=$(get_servers "${provider}" "${country}" "${city}")

    # Select the first server (could implement more sophisticated selection logic here)
    echo "${servers}" | jq -r 'if length > 0 then .[0].hostname else "" end'
}

# Validate if a country exists for a provider
validate_country() {
    local provider="$1"
    local country="$2"
    
    # Use get_countries to check if country exists
    get_countries "${provider}" 2>/dev/null | grep -qi "^${country}|" || \
    get_countries "${provider}" 2>/dev/null | grep -qi "|${country}$"
}

# Validate if a city exists for a provider
validate_city() {
    local provider="$1"
    local city="$2"
    
    if [[ -f "${SERVERS_CACHE_FILE}" ]]; then
        # Check if city exists in any country for this provider
        jq -r --arg provider "${provider}" --arg city "${city}" \
            '.[$provider].servers // [] | 
             map(.city // empty) | 
             map(select(. == $city)) | 
             if length > 0 then "found" else empty end' \
            "${SERVERS_CACHE_FILE}" | grep -q "found"
    else
        # Return success if no cache file
        return 0
    fi
}

# Find which country a city belongs to
find_country_for_city() {
    local provider="$1"
    local city="$2"
    
    if [[ -f "${SERVERS_CACHE_FILE}" ]]; then
        # Find the country code for this city
        jq -r --arg provider "${provider}" --arg city "${city}" \
            '.[$provider].servers // [] | 
             map(select(.city == $city)) | 
             if length > 0 then (.[0].country_code // .[0].region // empty) else empty end' \
            "${SERVERS_CACHE_FILE}" | head -1
    else
        # Return empty if no cache file
        echo ""
    fi
}

# Update the server list
update_server_list() {
    print_header "Updating server list..."

    # Force refresh of the server list
    rm -f "${SERVERS_CACHE_FILE}"
    fetch_server_list
}
