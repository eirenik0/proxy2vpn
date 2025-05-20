#!/bin/bash
#
# server_display.sh - Functions for displaying server information
# This file contains functions to display server list information to the user

# ======================================================
# Configuration Variables
# ======================================================

# Import these variables from the main script if they're not defined
# This prevents the "referenced but not assigned" shellcheck warnings
: "${SERVERS_CACHE_FILE:=${HOME}/.cache/proxy2vpn/gluetun_servers.json}"

# ======================================================
# Server Display Functions
# ======================================================

# List available providers
list_providers() {
    print_header "Available VPN Providers:"
    echo
    
    # Direct implementation to extract providers
    local tmp_file=$(mktemp)
    
    # Ensure the server list exists
    if ! [[ -f "${SERVERS_CACHE_FILE}" ]]; then
        fetch_server_list || return 1
    fi
    
    # Extract all providers except version
    jq -r 'keys | .[] | select(. != "version")' "${SERVERS_CACHE_FILE}" | sort > "${tmp_file}"
    
    # Count providers and print debug info BEFORE table headers
    local provider_count=$(wc -l < "${tmp_file}")
    print_info "DISPLAY: Listing all available VPN providers from server cache"
    print_debug "Cache file: ${SERVERS_CACHE_FILE}"
    print_debug "Found ${provider_count} providers"
    
    printf "%-25s\n" "PROVIDER"
    printf "%-25s\n" "--------"
    
    if [[ "${provider_count}" -eq 0 ]]; then
        print_warning "No providers found in server list"
    else
        # Display the providers
        while read -r provider; do
            printf "%-25s\n" "${provider}"
        done < "${tmp_file}"
    fi
    
    # Clean up
    rm -f "${tmp_file}"
}

# List available countries for a provider
list_countries() {
    local provider="${1:-${DEFAULT_VPN_PROVIDER}}"
    
    print_header "Available Countries for ${provider}:"
    echo
    
    # Direct implementation to extract countries
    local tmp_file=$(mktemp)
    
    # Extract unique countries from provider servers
    jq -r --arg provider "${provider}" '
        .[$provider].servers | 
        map({
            country: .country,
            # Better country code generation:
            # 1. Use country_code if available
            # 2. Use region if available
            # 3. Generate a 2-letter code from the country name
            code: (.country_code // 
                  .region // 
                  (if .country then 
                     (.country | split(" ") | .[0][0:2] | ascii_upcase) 
                   else 
                     "??" 
                   end)
            )
        }) | 
        unique_by(.country) | 
        sort_by(.country) | 
        .[] | 
        "\(.country)|\(.code)"
    ' "${SERVERS_CACHE_FILE}" > "${tmp_file}"
    
    # Count countries and display debug info BEFORE table headers
    local country_count=$(wc -l < "${tmp_file}")
    
    # Debug info before table headers
    print_info "DISPLAY: Listing countries for provider: ${provider}"
    print_debug "Cache file: ${SERVERS_CACHE_FILE}"
    if [[ -f "${SERVERS_CACHE_FILE}" ]]; then
        local provider_server_count=$(jq -r --arg provider "${provider}" '.[$provider].servers | length // 0' "${SERVERS_CACHE_FILE}")
        print_debug "Provider has ${provider_server_count} servers in cache"
    else
        print_warning "Server cache file not found or not accessible"
    fi
    print_debug "Found ${country_count} countries for ${provider}"
    
    printf "%-25s %-20s\n" "COUNTRY" "CODE"
    printf "%-25s %-20s\n" "-------" "----"
    
    if [[ "${country_count}" -eq 0 ]]; then
        print_warning "No countries found for ${provider}"
    else
        # Display the countries
        while IFS='|' read -r country code; do
            if [[ -z "${country}" ]]; then
                continue
            fi
            printf "%-25s %-20s\n" "${country}" "${code}"
        done < "${tmp_file}"
    fi
    
    # Clean up
    rm -f "${tmp_file}"
}

# List available cities for a country and provider
list_cities() {
    local provider="${1:-${DEFAULT_VPN_PROVIDER}}"
    local country_code="${2}"
    
    if [[ -z "${country_code}" ]]; then
        print_error "Country code is required"
        echo "Usage: ${0} list-cities <provider> <country_code>"
        return 1
    fi
    
    # Debug info for original input
    print_info "DISPLAY: Input country code: ${country_code}"
    
    # Special case mapping for common country codes
    case "${country_code}" in
        US | USA | UNITED_STATES | UNITEDSTATES)
            country_code="United States"
            print_debug "Country code normalized to: ${country_code}"
            ;;
        UK | GB | GREAT_BRITAIN | UNITED_KINGDOM | UNITEDKINGDOM)
            country_code="United Kingdom"
            print_debug "Country code normalized to: ${country_code}"
            ;;
        UAE | AE | UNITED_ARAB_EMIRATES)
            country_code="United Arab Emirates"
            print_debug "Country code normalized to: ${country_code}"
            ;;
        *)
            # Keep original country code
            print_debug "Using original country code: ${country_code}"
            ;;
    esac
    
    print_header "Available Cities for ${provider} in ${country_code}:"
    echo
    
    # Direct implementation to extract cities
    local tmp_file=$(mktemp)
    
    # Check if the provider exists
    if ! jq -e --arg provider "${provider}" '.[$provider]' "${SERVERS_CACHE_FILE}" >/dev/null 2>&1; then
        print_error "Provider ${provider} not found"
        rm -f "${tmp_file}"
        return 1
    fi
    
    # Extract unique cities for the country from provider servers
    jq -r --arg provider "${provider}" --arg country "${country_code}" '
        .[$provider].servers | 
        map(select(.country == $country or .country_code == $country)) |
        map(.city) | 
        map(select(. != null)) | 
        unique | 
        sort[]
    ' "${SERVERS_CACHE_FILE}" > "${tmp_file}"
    
    # Count cities and print ALL debug info before table headers
    local city_count=$(wc -l < "${tmp_file}")
    
    # Additional debug info before table headers
    print_info "DISPLAY: Listing cities for provider: ${provider}, country: ${country_code}"
    print_debug "Cache file: ${SERVERS_CACHE_FILE}"
    if [[ -f "${SERVERS_CACHE_FILE}" ]]; then
        local country_server_count=$(jq -r --arg provider "${provider}" --arg country "${country_code}" '.[$provider].servers | map(select(.country == $country or .country_code == $country)) | length // 0' "${SERVERS_CACHE_FILE}")
        print_debug "Provider has ${country_server_count} servers in ${country_code}"
    else
        print_warning "Server cache file not found or not accessible"
    fi
    print_debug "Found ${city_count} cities for ${provider} in ${country_code}"
    
    printf "%-25s\n" "CITY"
    printf "%-25s\n" "----"
    
    if [[ "${city_count}" -eq 0 ]]; then
        print_warning "No cities found for ${provider} in ${country_code}"
        # Try looking up with fuzzy match to help the user
        local matching_countries=$(jq -r --arg provider "${provider}" --arg country "${country_code}" \
            '.[$provider].servers | map(.country) | unique | map(select(. | ascii_downcase | contains($country | ascii_downcase))) | .[]' \
            "${SERVERS_CACHE_FILE}" | head -5)
        
        if [[ -n "${matching_countries}" ]]; then
            print_info "Try using one of these countries instead:"
            echo "${matching_countries}" | while read -r country; do
                print_info "  - ${country}"
            done
        fi
    else
        # Display the cities
        while read -r city; do
            if [[ -z "${city}" ]]; then
                continue
            fi
            printf "%-25s\n" "${city}"
        done < "${tmp_file}"
    fi
    
    # Clean up
    rm -f "${tmp_file}"
}

# List available servers for a provider, optionally filtered by country and city
list_servers() {
    local provider="${1:-${DEFAULT_VPN_PROVIDER}}"
    local country="${2:-}"
    local city="${3:-}"
    
    print_info "DISPLAY: Provider: ${provider}, Country: ${country}, City: ${city}"
    
    local location="${provider}"
    if [[ -n "${country}" ]]; then
        location="${location} in ${country}"
        if [[ -n "${city}" ]]; then
            location="${location}, ${city}"
        fi
    fi
    
    print_header "Available Servers for ${location}:"
    echo
    
    # Additional detailed debug information before listing servers
    print_info "DISPLAY: Server list query parameters"
    print_debug "Provider: ${provider}"
    print_debug "Country filter: ${country:-None}"
    print_debug "City filter: ${city:-None}"
    print_debug "Cache file: ${SERVERS_CACHE_FILE}"
    
    # Check the cache file exists and can be read
    if [[ ! -f "${SERVERS_CACHE_FILE}" ]]; then
        print_warning "Server cache file not found. Fetching server list..."
        fetch_server_list
    fi
    
    # Check provider exists in cache
    if ! jq -e --arg provider "${provider}" '.[$provider]' "${SERVERS_CACHE_FILE}" >/dev/null 2>&1; then
        print_warning "Provider ${provider} not found in cache file"
        print_debug "Available providers: $(jq -r 'keys | .[] | select(. != "version")' "${SERVERS_CACHE_FILE}" | tr '\n' ', ')"
        print_error "No servers found for provider: ${provider}"
        # Show available providers
        local available_providers=$(jq -r 'keys | join(", ")' "${SERVERS_CACHE_FILE}")
        print_info "DISPLAY: Available providers: ${available_providers}"
        return 1
    fi
    
    # Get total servers in the provider
    local total_provider_servers=$(jq -r --arg provider "${provider}" '.[$provider].servers | length // 0' "${SERVERS_CACHE_FILE}")
    print_debug "Total servers for ${provider}: ${total_provider_servers}"
    print_info "DISPLAY: Server count for ${provider}: ${total_provider_servers}"
    
    # If country filter applied, show count
    if [[ -n "${country}" ]]; then
        local country_servers=$(jq -r --arg provider "${provider}" --arg country "${country}" \
            '.[$provider].servers | map(select(.country == $country or .country_code == $country)) | length // 0' "${SERVERS_CACHE_FILE}")
        print_debug "Servers in ${country}: ${country_servers}"
        
        # If city filter applied, show count
        if [[ -n "${city}" ]]; then
            local city_servers=$(jq -r --arg provider "${provider}" --arg country "${country}" --arg city "${city}" \
                '.[$provider].servers | map(select(.country == $country and .city == $city)) | length // 0' "${SERVERS_CACHE_FILE}")
            print_debug "Servers in ${city}, ${country}: ${city_servers}"
        fi
    fi
    
    # Create a simpler listing directly without using the utility function
    # This will be more reliable
    local tmp_server_list=$(mktemp)
    
    # Extract servers to a temporary file with error handling
    if ! jq -r --arg provider "${provider}" \
       '.[$provider].servers | if . then map({hostname: (.hostname // .server_name), country: (.country), city: (.city), type: (.vpn // "openvpn")}) | map(select(.hostname != null)) else [] end' \
       "${SERVERS_CACHE_FILE}" > "${tmp_server_list}" 2>/dev/null; then
       
        # Handle the error
        print_error "Error processing server data for ${provider}"
        echo "[]" > "${tmp_server_list}"
    fi
    
    # Apply country filter if needed
    if [[ -n "${country}" ]]; then
        local tmp_country=$(mktemp)
        jq --arg country "${country}" '[.[] | select(.country == $country)]' "${tmp_server_list}" > "${tmp_country}"
        mv "${tmp_country}" "${tmp_server_list}"
    fi
    
    # Apply city filter if needed
    if [[ -n "${city}" ]]; then
        local tmp_city=$(mktemp)
        jq --arg city "${city}" '[.[] | select(.city == $city)]' "${tmp_server_list}" > "${tmp_city}"
        mv "${tmp_city}" "${tmp_server_list}"
    fi
    
    # Count the results and print info BEFORE the table headers
    local result_count=$(jq 'length' "${tmp_server_list}")
    print_info "DISPLAY: Filtered server count: ${result_count}"
    
    if [[ "${result_count}" -eq 0 ]]; then
        if [[ -n "${country}" ]]; then
            if [[ -n "${city}" ]]; then
                print_warning "No servers found for ${provider} in ${country}, ${city}"
            else
                print_warning "No servers found for ${provider} in ${country}"
            fi
        else
            print_warning "No servers found for ${provider}"
        fi
        rm -f "${tmp_server_list}"
        return 0
    fi
    
    # Now print the table headers
    printf "%-40s %-20s %-15s %-15s\n" "HOSTNAME" "COUNTRY" "CITY" "TYPE"
    printf "%-40s %-20s %-15s %-15s\n" "--------" "-------" "----" "----"
    
    if [[ "${result_count}" -eq 0 ]]; then
        if [[ -n "${country}" ]]; then
            if [[ -n "${city}" ]]; then
                print_warning "No servers found for ${provider} in ${country}, ${city}"
            else
                print_warning "No servers found for ${provider} in ${country}"
            fi
        else
            print_warning "No servers found for ${provider}"
        fi
        rm -f "${tmp_server_list}"
        return 0
    fi
    
    # Print the results
    jq -r '.[] | "\(.hostname)|\(.country)|\(.city // "N/A")|\(.type)"' "${tmp_server_list}" | \
    while IFS='|' read -r hostname country city type; do
        printf "%-40s %-20s %-15s %-15s\n" "${hostname}" "${country}" "${city}" "${type}"
    done
    
    # Check if we want to limit output for large server lists
    if [[ "${result_count}" -gt 20 ]]; then
        print_info "Showing 20 out of ${result_count} servers. Use 'list-servers ${provider} [country] [city]' to filter."
    fi
    
    # Clean up
    rm -f "${tmp_server_list}"
}