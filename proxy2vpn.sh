#!/bin/bash
#
# proxy2vpn.sh - Advanced script to manage multiple gluetun VPN containers
#
# Proxy2vpn supports user profiles, health monitoring, batch operations,
# and dynamic server list fetching.
# It manages multiple VPN containers using the qmcgaw/gluetun image.
#
# Usage: proxy2vpn.sh <command> [arguments]
#
# License: MIT

set -e

# ======================================================
# Configuration
# ======================================================
PREFIX="vpn"
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
PRESETS_FILE="${SCRIPT_DIR}/presets/presets.json"
PROFILES_DIR="${SCRIPT_DIR}/profiles"
CONFIG_FILE="${SCRIPT_DIR}/config.json"
HEALTH_CHECK_INTERVAL=60  # Check container health every 60 seconds
CACHE_DIR="${SCRIPT_DIR}/cache"
CACHE_TTL=86400  # Cache validity in seconds (24 hours)
GLUETUN_SERVERS_URL="https://raw.githubusercontent.com/qdm12/gluetun/master/internal/storage/servers.json"
SERVERS_CACHE_FILE="${CACHE_DIR}/gluetun_servers.json"

# Default config values (can be overridden in config.json)
DEFAULT_VPN_PROVIDER="protonvpn"
CONTAINER_NAMING_CONVENTION="numeric" # 'numeric' for vpn1, vpn2, etc. or 'descriptive' for vpn_us_east, etc.

# ======================================================
# Colors for output
# ======================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ======================================================
# Helper Functions
# ======================================================
print_error() {
    echo -e "${RED}Error: $1${NC}" >&2
}

print_success() {
    echo -e "${GREEN}$1${NC}"
}

print_info() {
    echo -e "${YELLOW}$1${NC}"
}

print_header() {
    echo -e "${BLUE}$1${NC}"
}

print_debug() {
    echo -e "${MAGENTA}Debug: $1${NC}"
}

print_config() {
    echo -e "${CYAN}Config: $1${NC}"
}

# Validate if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check dependencies
check_dependencies() {
    if ! command_exists docker; then
        print_error "Docker is not installed. Please install Docker to use this script."
        exit 1
    fi

    if ! command_exists jq; then
        print_error "jq is not installed but required for preset functionality and server list parsing."
        print_info "Install jq with: brew install jq (macOS) or apt install jq (Ubuntu/Debian)"
        exit 1
    fi

    if ! command_exists curl; then
        print_error "curl is not installed but required for testing connections and fetching server lists."
        print_info "Install curl with: brew install curl (macOS) or apt install curl (Ubuntu/Debian)"
        exit 1
    fi
}

# Initialize folders and configuration
init_environment() {
    # Create profiles directory if it doesn't exist
    if [ ! -d "$PROFILES_DIR" ]; then
        print_info "Creating profiles directory: $PROFILES_DIR"
        mkdir -p "$PROFILES_DIR"
    fi

    # Create cache directory if it doesn't exist
    if [ ! -d "$CACHE_DIR" ]; then
        print_info "Creating cache directory: $CACHE_DIR"
        mkdir -p "$CACHE_DIR"
    fi

    # Create a default config file if it doesn't exist
    if [ ! -f "$CONFIG_FILE" ]; then
        print_info "Creating default configuration file: $CONFIG_FILE"
        cat > "$CONFIG_FILE" << EOF
{
    "default_vpn_provider": "${DEFAULT_VPN_PROVIDER}",
    "container_naming_convention": "${CONTAINER_NAMING_CONVENTION}",
    "default_proxy_port": 8888,
    "health_check_interval": ${HEALTH_CHECK_INTERVAL},
    "auto_restart_containers": true,
    "use_device_tun": true,
    "server_cache_ttl": ${CACHE_TTL},
    "validate_server_locations": true
}
EOF
    fi

    # Load configuration from file
    if [ -f "$CONFIG_FILE" ]; then
        DEFAULT_VPN_PROVIDER=$(jq -r '.default_vpn_provider // "protonvpn"' "$CONFIG_FILE")
        CONTAINER_NAMING_CONVENTION=$(jq -r '.container_naming_convention // "numeric"' "$CONFIG_FILE")
        HEALTH_CHECK_INTERVAL=$(jq -r '.health_check_interval // 60' "$CONFIG_FILE")
        CACHE_TTL=$(jq -r '.server_cache_ttl // 86400' "$CONFIG_FILE")
    fi
}

# Create Docker network if it doesn't exist
ensure_network() {
    local network_name="${PREFIX}_network"
    if ! docker network inspect "$network_name" &>/dev/null; then
        print_info "Creating Docker network: $network_name"
        docker network create "$network_name" >/dev/null
    fi
}

# ======================================================
# VPN Server List Management
# ======================================================

# Fetch and cache server list from gluetun
fetch_server_list() {
    print_info "Fetching VPN server list from gluetun..."
    
    # Check if the cache exists and is still valid
    if [ -f "$SERVERS_CACHE_FILE" ]; then
        local file_age=$(($(date +%s) - $(date -r "$SERVERS_CACHE_FILE" +%s)))
        if [ $file_age -lt $CACHE_TTL ]; then
            print_info "Using cached server list (age: $(($file_age / 60 / 60)) hours)"
            return 0
        else
            print_info "Cached server list is outdated, refreshing..."
        fi
    else
        print_info "No cached server list found, downloading..."
    fi
    
    # Fetch server list from GitHub
    if ! curl -s -o "$SERVERS_CACHE_FILE" "$GLUETUN_SERVERS_URL"; then
        print_error "Failed to download server list from $GLUETUN_SERVERS_URL"
        return 1
    fi
    
    # Validate that the file is valid JSON
    if ! jq empty "$SERVERS_CACHE_FILE" >/dev/null 2>&1; then
        print_error "Downloaded server list is not valid JSON"
        rm -f "$SERVERS_CACHE_FILE"
        return 1
    fi
    
    print_success "Server list fetched and cached successfully"
    return 0
}

# Extract countries list for a provider
get_countries_for_provider() {
    local provider="$1"
    
    # Ensure we have the server list
    if ! [ -f "$SERVERS_CACHE_FILE" ]; then
        fetch_server_list || return 1
    fi
    
    # Create provider-specific cache file for countries
    local cache_file="${CACHE_DIR}/${provider}_countries.json"
    
    # Check if the cache file exists and is valid
    if [ -f "$cache_file" ]; then
        local file_age=$(($(date +%s) - $(date -r "$cache_file" +%s)))
        if [ $file_age -lt $CACHE_TTL ]; then
            return 0
        fi
    fi
    
    print_info "Extracting countries for $provider..."
    
    # Extract countries based on provider from the server list
    if [[ "$provider" == "private internet access" ]]; then
        provider="pia"
    elif [[ "$provider" == "private internet access" ]]; then
        provider="pia"
    fi
    
    # Process providers with different schemas
    if jq -e --arg provider "$provider" '.[$provider]' "$SERVERS_CACHE_FILE" >/dev/null 2>&1; then
        jq -r --arg provider "$provider" '
            .[$provider].servers | 
            group_by(.country) | 
            map({
                country: (.[0].country // "Unknown"),
                code: (.[0].country_code // .[0].region // 
                      (if .[0].country then (.[0].country | split(" ") | .[0][0:2] | ascii_upcase) else "??" end))
            }) | 
            unique_by(.country)
        ' "$SERVERS_CACHE_FILE" > "$cache_file"
    else
        print_error "Provider $provider not found in server list"
        echo "[]" > "$cache_file"
        return 1
    fi
    
    print_success "Extracted and cached countries for $provider"
    return 0
}

# Extract cities for a provider in a country
get_cities_for_country() {
    local provider="$1"
    local country_code="$2"
    
    # Ensure we have the server list
    if ! [ -f "$SERVERS_CACHE_FILE" ]; then
        fetch_server_list || return 1
    fi
    
    # Create provider-specific cache file for cities
    local cache_file="${CACHE_DIR}/${provider}_cities.json"
    
    # Check if the cache file exists and is valid
    if [ -f "$cache_file" ]; then
        local file_age=$(($(date +%s) - $(date -r "$cache_file" +%s)))
        if [ $file_age -lt $CACHE_TTL ]; then
            return 0
        fi
    fi
    
    print_info "Extracting cities for $provider..."
    
    # Normalize provider name for lookup
    if [[ "$provider" == "private internet access" ]]; then
        provider="pia"
    fi
    
    # Process providers with different schemas
    if jq -e --arg provider "$provider" '.[$provider]' "$SERVERS_CACHE_FILE" >/dev/null 2>&1; then
        jq -r --arg provider "$provider" '
            .[$provider].servers | 
            group_by(.country) | 
            map({
                country_code: (.[0].country_code // .[0].region // 
                              (if .[0].country then (.[0].country | split(" ") | .[0][0:2] | ascii_upcase) else "??" end)),
                country: (.[0].country // "Unknown"),
                cities: (
                    map(.city) | 
                    map(select(. != null)) | 
                    unique | 
                    sort
                )
            })
        ' "$SERVERS_CACHE_FILE" > "$cache_file"
    else
        print_error "Provider $provider not found in server list"
        echo "[]" > "$cache_file"
        return 1
    fi
    
    print_success "Extracted and cached cities for $provider"
    return 0
}

# Get server hostnames for a provider in a city
get_servers_for_city() {
    local provider="$1"
    local country_code="$2"
    local city="$3"
    
    # Ensure we have the server list
    if ! [ -f "$SERVERS_CACHE_FILE" ]; then
        fetch_server_list || return 1
    fi
    
    # Create provider-specific cache file for server hostnames
    local cache_file="${CACHE_DIR}/${provider}_format_servers.json"
    
    # Check if the cache file exists and is valid
    if [ -f "$cache_file" ]; then
        local file_age=$(($(date +%s) - $(date -r "$cache_file" +%s)))
        if [ $file_age -lt $CACHE_TTL ]; then
            return 0
        fi
    fi
    
    print_info "Extracting server hostnames for $provider..."
    
    # Normalize provider name for lookup
    if [[ "$provider" == "private internet access" ]]; then
        provider="pia"
    fi
    
    # Process providers with different schemas
    if jq -e --arg provider "$provider" '.[$provider]' "$SERVERS_CACHE_FILE" >/dev/null 2>&1; then
        jq -r --arg provider "$provider" '
            .[$provider].servers |
            map({
                hostname: (.hostname // .server_name // null),
                country: (.country // "Unknown"),
                country_code: (.country_code // .region // "??"),
                city: (.city // null),
                ip: (if .ips then .ips[0] else null end),
                type: (.vpn // "openvpn")
            }) |
            map(select(.hostname != null))
        ' "$SERVERS_CACHE_FILE" > "$cache_file"
    else
        print_error "Provider $provider not found in server list"
        echo "[]" > "$cache_file"
        return 1
    fi
    
    print_success "Extracted and cached servers for $provider"
    return 0
}

# Validate if a country exists for a provider
validate_country() {
    local provider="$1"
    local country="$2"
    
    # Ensure we have countries for this provider
    get_countries_for_provider "$provider" || return 1
    
    local country_file="${CACHE_DIR}/${provider}_countries.json"
    if ! [ -f "$country_file" ]; then
        print_error "Country cache file not found"
        return 1
    fi
    
    # Check if the country exists in the provider's list
    if jq -e --arg country "$country" '.[] | select(.country == $country or .code == $country)' "$country_file" >/dev/null 2>&1; then
        return 0
    else
        print_error "Country '$country' not found for provider '$provider'"
        return 1
    fi
}

# Validate if a city exists within a country for a provider
validate_city() {
    local provider="$1"
    local country_code="$2"
    local city="$3"
    
    # Ensure we have cities for this provider
    get_cities_for_country "$provider" "$country_code" || return 1
    
    local city_file="${CACHE_DIR}/${provider}_cities.json"
    if ! [ -f "$city_file" ]; then
        print_error "City cache file not found"
        return 1
    fi
    
    # Check if the city exists in the provider's list for the specified country
    if jq -e --arg country "$country_code" --arg city "$city" '.[] | select(.country_code == $country or .country == $country) | .cities | index($city)' "$city_file" >/dev/null 2>&1; then
        return 0
    else
        print_error "City '$city' not found in country '$country_code' for provider '$provider'"
        return 1
    fi
}

# List available countries for a provider
list_countries() {
    local provider="${1:-$DEFAULT_VPN_PROVIDER}"
    
    # Ensure we have countries for this provider
    get_countries_for_provider "$provider" || return 1
    
    local country_file="${CACHE_DIR}/${provider}_countries.json"
    if ! [ -f "$country_file" ]; then
        print_error "Country cache file not found"
        return 1
    fi
    
    print_header "Available Countries for $provider:"
    echo
    
    printf "%-25s %-20s\n" "COUNTRY" "CODE"
    printf "%-25s %-20s\n" "-------" "----"
    
    jq -r '.[] | "\(.country)|\(.code)"' "$country_file" | while read -r line; do
        country=$(echo "$line" | cut -d'|' -f1)
        code=$(echo "$line" | cut -d'|' -f2)
        printf "%-25s %-20s\n" "$country" "$code"
    done
}

# List available cities for a country and provider
list_cities() {
    local provider="${1:-$DEFAULT_VPN_PROVIDER}"
    local country_code="$2"
    
    if [ -z "$country_code" ]; then
        print_error "Country code is required"
        echo "Usage: $0 list-cities <provider> <country_code>"
        return 1
    fi
    
    # Special case mapping for common country codes
    case "$country_code" in
        US|USA|UNITED_STATES|UNITEDSTATES)
            country_code="United States"
            ;;
        UK|GB|GREAT_BRITAIN|UNITED_KINGDOM|UNITEDKINGDOM)
            country_code="United Kingdom"
            ;;
        UAE|AE|UNITED_ARAB_EMIRATES)
            country_code="United Arab Emirates"
            ;;
    esac
    
    # Ensure we have cities for this provider
    get_cities_for_country "$provider" "$country_code" || return 1
    
    local city_file="${CACHE_DIR}/${provider}_cities.json"
    if ! [ -f "$city_file" ]; then
        print_error "City cache file not found"
        return 1
    fi
    
    print_header "Available Cities for $provider in $country_code:"
    echo
    
    printf "%-30s\n" "CITY"
    printf "%-30s\n" "----"
    
    # Extract cities for the specified country
    # First try exact country code, then try fuzzy match with country name
    country_name=$(jq -r --arg code "$country_code" '.[] | select(.country_code == $code) | .country' "$city_file" | head -1)

    # If a 2-letter code was provided but no match found, try to find matching countries
    if [ -z "$country_name" ] && [[ ${#country_code} -eq 2 ]]; then
        country_name=$(jq -r --arg code "$country_code" '.[] | select(.country | test("^" + $code; "i")) | .country' "$city_file" | head -1)
        
        # Try matching countries that start with this code
        if [ -z "$country_name" ]; then
            country_name=$(jq -r '.[] | .country' "$city_file" | grep -i "^$country_code" | head -1)
        fi
        
        # As a last resort, look for countries that have this code
        if [ -z "$country_name" ]; then
            country_code_upper=$(echo "$country_code" | tr '[:lower:]' '[:upper:]')
            country_name=$(jq -r --arg code "$country_code_upper" '.[] | select(.country_code == $code) | .country' "$city_file" | head -1)
        fi
    fi
    
    if [ -n "$country_name" ]; then
        print_info "Found matching country: $country_name"
        jq -r --arg country "$country_name" '.[] | select(.country == $country) | .cities[]' "$city_file" | sort | while read -r city; do
            printf "%-30s\n" "$city"
        done
    else
        # Fallback to original search
        jq -r --arg country "$country_code" '.[] | select(.country_code == $country or .country == $country) | .cities[]' "$city_file" | sort | while read -r city; do
            printf "%-30s\n" "$city"
        done
    fi
}

# Update all server lists for all supported providers
update_all_server_lists() {
    print_header "Updating server lists for all providers..."
    echo
    
    # Fetch main server list
    fetch_server_list || return 1
    
    # Extract all providers from the server list
    local providers=$(jq -r 'keys | .[]' "$SERVERS_CACHE_FILE" | grep -v "version" | sort | uniq)
    
    for provider in $providers; do
        print_info "Updating data for provider: $provider"
        get_countries_for_provider "$provider"
        get_cities_for_country "$provider" ""
        get_servers_for_city "$provider" "" ""
    done
    
    print_success "All server lists updated successfully"
    return 0
}

# ======================================================
# User Profiles Management
# ======================================================

# Create a user profile
create_profile() {
    local profile_name="$1"
    local username="$2"
    local password="$3"

    if [ -z "$profile_name" ] || [ -z "$username" ] || [ -z "$password" ]; then
        print_error "Missing required parameters."
        echo "Usage: $0 create-profile <profile_name> <username> <password>"
        exit 1
    fi

    local profile_file="${PROFILES_DIR}/${profile_name}.env"

    # Check if profile already exists
    if [ -f "$profile_file" ]; then
        print_error "Profile '$profile_name' already exists"
        exit 1
    fi

    # Create profile file
    cat > "$profile_file" << EOF
OPENVPN_USER=$username
OPENVPN_PASSWORD=$password
EOF

    print_success "Created user profile: $profile_name"
}

# List all user profiles
list_profiles() {
    if [ ! -d "$PROFILES_DIR" ] || [ -z "$(ls -A "$PROFILES_DIR")" ]; then
        print_info "No user profiles found"
        return
    fi

    print_header "User Profiles:"
    echo

    printf "%-20s %-30s\n" "PROFILE NAME" "USERNAME"
    printf "%-20s %-30s\n" "------------" "--------"

    for profile in "$PROFILES_DIR"/*.env; do
        local profile_name=$(basename "$profile" .env)
        local username=$(grep "OPENVPN_USER" "$profile" | cut -d'=' -f2)

        if [ -z "$username" ]; then username="<empty>"; fi

        printf "%-20s %-30s\n" "$profile_name" "$username"
    done
}

# ======================================================
# VPN Container Management
# ======================================================

# Create a VPN container with a profile
create_vpn_from_profile() {
    local container_name="$1"
    local port="$2"
    local profile_name="$3"
    local server_city="$4"
    local server_hostname="$5"
    local provider="${6:-$DEFAULT_VPN_PROVIDER}"

    if [ -z "$container_name" ] || [ -z "$port" ] || [ -z "$profile_name" ]; then
        print_error "Missing required parameters."
        echo "Usage: $0 create-from-profile <container_name> <port> <profile_name> [server_city] [server_hostname] [provider]"
        echo "Example: $0 create-from-profile vpn1 8888 myprofile \"New York\" \"\" protonvpn"
        exit 1
    fi

    # Check if profile exists
    local profile_file="${PROFILES_DIR}/${profile_name}.env"
    if [ ! -f "$profile_file" ]; then
        print_error "Profile '$profile_name' not found"
        exit 1
    fi

    # Full container name - if using numeric naming convention, keep as is
    local full_container_name="$container_name"
    if [[ ! "$container_name" == vpn* ]]; then
        full_container_name="${PREFIX}_${container_name}"
    fi

    # Check if container already exists
    if docker ps -a --format '{{.Names}}' | grep -q "^${full_container_name}$"; then
        print_error "Container ${full_container_name} already exists"
        exit 1
    fi

    # Create environment variable array
    env_vars=(
        "-e VPN_SERVICE_PROVIDER=${provider}"
        "-e HTTPPROXY=on"
        "-e HTTPPROXY_LISTENING_ADDRESS=:8888"
        "--env-file ${profile_file}"
    )

    # Add optional location parameters
    if [ -n "$server_city" ]; then
        # Validate city if server validation is enabled
        if jq -e '.validate_server_locations // true' "$CONFIG_FILE" | grep -q "true"; then
            # Fetch server list if needed
            if ! [ -f "$SERVERS_CACHE_FILE" ]; then
                fetch_server_list || exit 1
            fi
            
            # Get country for the city
            local country_code=""
            if [ -f "${CACHE_DIR}/${provider}_cities.json" ]; then
                country_code=$(jq -r --arg city "$server_city" '.[] | select(.cities | index($city) >= 0) | .country_code' "${CACHE_DIR}/${provider}_cities.json" | head -1)
            fi
            
            if [ -n "$country_code" ]; then
                env_vars+=("-e SERVER_COUNTRIES=${country_code}")
            fi
        fi
        
        env_vars+=("-e SERVER_CITIES=${server_city}")
        location_type="city"
        location_value="$server_city"
    elif [ -n "$server_hostname" ]; then
        env_vars+=("-e SERVER_HOSTNAMES=${server_hostname}")
        location_type="hostname"
        location_value="$server_hostname"
    fi

    # Add device tun if configured
    if jq -e '.use_device_tun // true' "$CONFIG_FILE" | grep -q "true"; then
        device_args="--device /dev/net/tun:/dev/net/tun"
    else
        device_args=""
    fi

    # Ensure network exists
    ensure_network

    print_info "Creating VPN container ${container_name} on port ${port}..."

    # Create the container
    docker run -d \
        --name "$full_container_name" \
        --restart unless-stopped \
        -p "${port}:8888" \
        --cap-add=NET_ADMIN \
        ${device_args} \
        --network "${PREFIX}_network" \
        --label "${PREFIX}.type=vpn" \
        --label "${PREFIX}.port=${port}" \
        --label "${PREFIX}.internal_port=8888" \
        --label "${PREFIX}.provider=${provider}" \
        --label "${PREFIX}.profile=${profile_name}" \
        --label "${PREFIX}.location_type=${location_type}" \
        --label "${PREFIX}.location=${location_value}" \
        ${env_vars[@]} \
        qmcgaw/gluetun:latest

    local exit_code=$?
    if [ $exit_code -eq 0 ]; then
        print_success "VPN container ${container_name} created successfully on port ${port}"
        print_info "To test the connection: curl -x localhost:${port} https://ifconfig.me"
    else
        print_error "Failed to create container"
        exit $exit_code
    fi
}

# Create a regular VPN container with validation
create_vpn() {
    local name="$1"
    local port="$2"
    local provider="$3"
    local location="$4"
    local username="${5:-}"
    local password="${6:-}"

    if [ -z "$name" ] || [ -z "$port" ] || [ -z "$provider" ]; then
        print_error "Missing required parameters."
        echo "Usage: $0 create <container_name> <port> <provider> [location] [username] [password]"
        echo "Example: $0 create vpn1 8888 protonvpn \"United States\" myuser mypass"
        exit 1
    fi

    # Full container name - if using numeric naming convention, keep as is
    local full_container_name="$name"
    if [[ ! "$name" == vpn* ]]; then
        full_container_name="${PREFIX}_${name}"
    fi

    # Check if container already exists
    if docker ps -a --format '{{.Names}}' | grep -q "^${full_container_name}$"; then
        print_error "Container ${full_container_name} already exists"
        exit 1
    fi

    # Create environment variable array
    env_vars=(
        "-e VPN_SERVICE_PROVIDER=${provider}"
        "-e HTTPPROXY=on"
        "-e HTTPPROXY_LISTENING_ADDRESS=:8888"
    )

    # Add optional location with validation
    if [ -n "$location" ]; then
        # Fetch server list if needed
        if ! [ -f "$SERVERS_CACHE_FILE" ]; then
            fetch_server_list || exit 1
        fi
        
        # Try to determine if location is a country, city, or hostname
        if jq -e '.validate_server_locations // true' "$CONFIG_FILE" | grep -q "true"; then
            # Check if it's a country
            if validate_country "$provider" "$location"; then
                env_vars+=("-e SERVER_COUNTRIES=${location}")
                location_type="country"
            else
                # Check major cities
                get_cities_for_country "$provider" ""
                
                # See if any cities match
                if [ -f "${CACHE_DIR}/${provider}_cities.json" ]; then
                    if jq -e --arg city "$location" '.[] | select(.cities | index($city) >= 0)' "${CACHE_DIR}/${provider}_cities.json" >/dev/null 2>&1; then
                        env_vars+=("-e SERVER_CITIES=${location}")
                        location_type="city"
                        
                        # Also try to find which country this city belongs to
                        local country_code=$(jq -r --arg city "$location" '.[] | select(.cities | index($city) >= 0) | .country_code' "${CACHE_DIR}/${provider}_cities.json" | head -1)
                        if [ -n "$country_code" ]; then
                            env_vars+=("-e SERVER_COUNTRIES=${country_code}")
                        fi
                    else
                        # Assume it's a hostname or just pass it as country anyway
                        env_vars+=("-e SERVER_COUNTRIES=${location}")
                        location_type="country"
                        print_warning "Location '$location' not found in provider server list. Using as country name anyway."
                    fi
                else
                    # If no city file, just use as country
                    env_vars+=("-e SERVER_COUNTRIES=${location}")
                    location_type="country"
                fi
            fi
        else
            # If validation is disabled, just use as country
            env_vars+=("-e SERVER_COUNTRIES=${location}")
            location_type="country"
        fi
    fi

    if [ -n "$username" ]; then
        env_vars+=("-e OPENVPN_USER=${username}")
    fi

    if [ -n "$password" ]; then
        env_vars+=("-e OPENVPN_PASSWORD=${password}")
    fi

    # Add device tun if configured
    if jq -e '.use_device_tun // true' "$CONFIG_FILE" | grep -q "true"; then
        device_args="--device /dev/net/tun:/dev/net/tun"
    else
        device_args=""
    fi

    # Ensure network exists
    ensure_network

    print_info "Creating VPN container ${name} on port ${port}..."

    # Create the container
    docker run -d \
        --name "$full_container_name" \
        --restart unless-stopped \
        -p "${port}:8888" \
        --cap-add=NET_ADMIN \
        ${device_args} \
        --network "${PREFIX}_network" \
        --label "${PREFIX}.type=vpn" \
        --label "${PREFIX}.port=${port}" \
        --label "${PREFIX}.internal_port=8888" \
        --label "${PREFIX}.provider=${provider}" \
        --label "${PREFIX}.location=${location}" \
        --label "${PREFIX}.location_type=${location_type}" \
        ${env_vars[@]} \
        qmcgaw/gluetun:latest

    local exit_code=$?
    if [ $exit_code -eq 0 ]; then
        print_success "VPN container ${name} created successfully on port ${port}"
        print_info "To test the connection: curl -x localhost:${port} https://ifconfig.me"
    else
        print_error "Failed to create container"
        exit $exit_code
    fi
}

# List all VPN containers with details
list_containers() {
    print_header "VPN Containers:"
    echo

    # Check if there are any containers with our label
    if ! docker ps -a --filter "label=${PREFIX}.type=vpn" --format "{{.Names}}" | grep -q .; then
        print_info "No VPN containers found"
        return
    fi

    # Print table header
    printf "%-15s %-10s %-12s %-15s %-10s %-15s %-20s %-10s\n" "NAME" "PORT" "PROVIDER" "PROFILE" "LOC TYPE" "LOCATION" "IP" "STATUS"
    printf "%-15s %-10s %-12s %-15s %-10s %-15s %-20s %-10s\n" "----" "----" "--------" "-------" "--------" "--------" "--" "------"

    # Find all containers with our labels
    local containers=$(docker ps -a --filter "label=${PREFIX}.type=vpn" --format "{{.Names}}")

    for container in $containers; do
        local short_name=$(basename "$container")
        if [[ "$short_name" == "${PREFIX}_"* ]]; then
            short_name=$(echo "$short_name" | sed "s/^${PREFIX}_//")
        fi

        local port=$(docker inspect --format='{{index .Config.Labels "'${PREFIX}'.port"}}' "$container" 2>/dev/null)
        local provider=$(docker inspect --format='{{index .Config.Labels "'${PREFIX}'.provider"}}' "$container" 2>/dev/null)
        local profile=$(docker inspect --format='{{index .Config.Labels "'${PREFIX}'.profile" | printf "%s"}}' "$container" 2>/dev/null)
        local location_type=$(docker inspect --format='{{index .Config.Labels "'${PREFIX}'.location_type" | printf "%s"}}' "$container" 2>/dev/null)
        local location=$(docker inspect --format='{{index .Config.Labels "'${PREFIX}'.location"}}' "$container" 2>/dev/null)
        local status=$(docker inspect --format='{{.State.Status}}' "$container" 2>/dev/null)

        # Get IP address if container is running
        local ip="N/A"
        if [ "$status" = "running" ]; then
            ip=$(curl -s -m 5 -x "localhost:${port}" https://ifconfig.me 2>/dev/null || echo "N/A")
        fi

        if [ -z "$port" ]; then port="N/A"; fi
        if [ -z "$provider" ]; then provider="unknown"; fi
        if [ -z "$profile" ]; then profile="none"; fi
        if [ -z "$location_type" ]; then
            if [ -n "$location" ]; then
                location_type="country";
            else
                location_type="none";
            fi
        fi
        if [ -z "$location" ]; then location="unspecified"; fi

        printf "%-15s %-10s %-12s %-15s %-10s %-15s %-20s %-10s\n" "$short_name" "$port" "$provider" "$profile" "$location_type" "$location" "$ip" "$status"
    done
}

# Delete a VPN container
delete_container() {
    local name="$1"

    if [ -z "$name" ]; then
        print_error "Missing container name."
        echo "Usage: $0 delete <container_name>"
        exit 1
    fi

    # Full container name - if it already starts with vpn, keep as is
    local full_container_name="$name"
    if [[ ! "$name" == vpn* ]]; then
        full_container_name="${PREFIX}_${name}"
    fi

    # Check if container exists
    if ! docker ps -a --format '{{.Names}}' | grep -q "^${full_container_name}$"; then
        print_error "Container ${full_container_name} does not exist"
        exit 1
    fi

    print_info "Deleting VPN container ${name}..."

    # Stop and remove the container
    docker stop "$full_container_name" >/dev/null
    docker rm "$full_container_name" >/dev/null

    print_success "Container ${name} deleted successfully"
}

# View container logs
view_logs() {
    local name="$1"
    local lines="${2:-100}"

    if [ -z "$name" ]; then
        print_error "Missing container name."
        echo "Usage: $0 logs <container_name> [lines]"
        exit 1
    fi

    # Full container name - if it already starts with vpn, keep as is
    local full_container_name="$name"
    if [[ ! "$name" == vpn* ]]; then
        full_container_name="${PREFIX}_${name}"
    fi

    # Check if container exists
    if ! docker ps -a --format '{{.Names}}' | grep -q "^${full_container_name}$"; then
        print_error "Container ${full_container_name} does not exist"
        exit 1
    fi

    print_info "Logs for container ${name} (last ${lines} lines):"
    echo

    docker logs --tail "$lines" "$full_container_name"
}

# Update container configuration with validation
update_container() {
    local name="$1"
    local key="$2"
    local value="$3"

    if [ -z "$name" ] || [ -z "$key" ] || [ -z "$value" ]; then
        print_error "Missing required parameters."
        echo "Usage: $0 update <container_name> <key> <value>"
        echo "Example: $0 update vpn1 SERVER_CITIES \"New York\""
        exit 1
    fi

    # Full container name - if it already starts with vpn, keep as is
    local full_container_name="$name"
    if [[ ! "$name" == vpn* ]]; then
        full_container_name="${PREFIX}_${name}"
    fi

    # Check if container exists
    if ! docker ps -a --format '{{.Names}}' | grep -q "^${full_container_name}$"; then
        print_error "Container ${full_container_name} does not exist"
        exit 1
    fi

    print_info "Updating container ${name} setting ${key}=${value}..."

    # Need to recreate the container with the new environment variable
    # First, get all the current container information
    local port=$(docker inspect --format='{{index .Config.Labels "'${PREFIX}'.port"}}' "$full_container_name")
    local provider=$(docker inspect --format='{{index .Config.Labels "'${PREFIX}'.provider"}}' "$full_container_name")
    local profile=$(docker inspect --format='{{index .Config.Labels "'${PREFIX}'.profile" | printf "%s"}}' "$full_container_name")
    local location_type=$(docker inspect --format='{{index .Config.Labels "'${PREFIX}'.location_type" | printf "%s"}}' "$full_container_name")
    local location=$(docker inspect --format='{{index .Config.Labels "'${PREFIX}'.location"}}' "$full_container_name")

    # Get all environment variables
    local env_vars=$(docker inspect --format='{{range .Config.Env}}{{.}} {{end}}' "$full_container_name")

    # Create an array to hold new environment variables
    local new_env_vars=()

    # Process existing env vars and replace the one being updated
    updated=false
    for env in $env_vars; do
        # Skip empty values
        if [ -z "$env" ]; then
            continue
        fi

        # Split into key and value (handle empty values correctly)
        local env_key=$(echo "$env" | cut -d= -f1)
        local env_value=$(echo "$env" | cut -d= -f2-)

        if [ "$env_key" = "$key" ]; then
            new_env_vars+=("-e ${key}=${value}")
            updated=true
        else
            new_env_vars+=("-e ${env_key}=${env_value}")
        fi
    done

    # If the variable wasn't in the existing set, add it
    if [ "$updated" = "false" ]; then
        new_env_vars+=("-e ${key}=${value}")
    fi

    # Update location labels based on what was updated
    if [ "$key" = "SERVER_COUNTRIES" ]; then
        # Validate country if server validation is enabled
        if jq -e '.validate_server_locations // true' "$CONFIG_FILE" | grep -q "true"; then
            if ! validate_country "$provider" "$value"; then
                print_warning "Country '$value' not found in provider server list. Using anyway."
            fi
        fi
        
        location="$value"
        location_type="country"
    elif [ "$key" = "SERVER_CITIES" ]; then
        # Validate city if server validation is enabled
        if jq -e '.validate_server_locations // true' "$CONFIG_FILE" | grep -q "true"; then
            get_cities_for_country "$provider" ""
            
            # Try to find which country this city belongs to
            if [ -f "${CACHE_DIR}/${provider}_cities.json" ]; then
                local country_code=$(jq -r --arg city "$value" '.[] | select(.cities | index($city) >= 0) | .country_code' "${CACHE_DIR}/${provider}_cities.json" | head -1)
                
                if [ -n "$country_code" ]; then
                    # Also update the country if we found a match
                    new_env_vars+=("-e SERVER_COUNTRIES=${country_code}")
                else
                    print_warning "City '$value' not found in any country for provider '$provider'. Using anyway."
                fi
            fi
        fi
        
        location="$value"
        location_type="city"
    elif [ "$key" = "SERVER_HOSTNAMES" ]; then
        location="$value"
        location_type="hostname"
    fi

    # Add device tun if configured
    if jq -e '.use_device_tun // true' "$CONFIG_FILE" | grep -q "true"; then
        device_args="--device /dev/net/tun:/dev/net/tun"
    else
        device_args=""
    fi

    # Stop and remove the old container
    docker stop "$full_container_name" >/dev/null
    docker rm "$full_container_name" >/dev/null

    # Create a new container with the updated configuration
    docker run -d \
        --name "$full_container_name" \
        --restart unless-stopped \
        -p "${port}:8888" \
        --cap-add=NET_ADMIN \
        ${device_args} \
        --network "${PREFIX}_network" \
        --label "${PREFIX}.type=vpn" \
        --label "${PREFIX}.port=${port}" \
        --label "${PREFIX}.internal_port=8888" \
        --label "${PREFIX}.provider=${provider}" \
        --label "${PREFIX}.profile=${profile}" \
        --label "${PREFIX}.location_type=${location_type}" \
        --label "${PREFIX}.location=${location}" \
        ${new_env_vars[@]} \
        qmcgaw/gluetun:latest

    print_success "Container ${name} updated successfully"
    print_info "Note: The container has been recreated with the new settings"
}

# Start a container
start_container() {
    local name="$1"

    if [ -z "$name" ]; then
        print_error "Missing container name."
        echo "Usage: $0 start <container_name>"
        exit 1
    fi

    # Full container name - if it already starts with vpn, keep as is
    local full_container_name="$name"
    if [[ ! "$name" == vpn* ]]; then
        full_container_name="${PREFIX}_${name}"
    fi

    # Check if container exists
    if ! docker ps -a --format '{{.Names}}' | grep -q "^${full_container_name}$"; then
        print_error "Container ${full_container_name} does not exist"
        exit 1
    fi

    # Check if already running
    if docker ps --format '{{.Names}}' | grep -q "^${full_container_name}$"; then
        print_info "Container ${name} is already running"
        return
    fi

    print_info "Starting container ${name}..."
    docker start "$full_container_name" >/dev/null
    print_success "Container ${name} started successfully"
}

# Stop a container
stop_container() {
    local name="$1"

    if [ -z "$name" ]; then
        print_error "Missing container name."
        echo "Usage: $0 stop <container_name>"
        exit 1
    fi

    # Full container name - if it already starts with vpn, keep as is
    local full_container_name="$name"
    if [[ ! "$name" == vpn* ]]; then
        full_container_name="${PREFIX}_${name}"
    fi

    # Check if container exists
    if ! docker ps -a --format '{{.Names}}' | grep -q "^${full_container_name}$"; then
        print_error "Container ${full_container_name} does not exist"
        exit 1
    fi

    # Check if already stopped
    if ! docker ps --format '{{.Names}}' | grep -q "^${full_container_name}$"; then
        print_info "Container ${name} is already stopped"
        return
    fi

    print_info "Stopping container ${name}..."
    docker stop "$full_container_name" >/dev/null
    print_success "Container ${name} stopped successfully"
}

# ======================================================
# Monitoring and Health Checks
# ======================================================

# Test a proxy connection
test_proxy() {
    local port="${1:-8888}"
    local host="${2:-localhost}"
    local url="${3:-https://ifconfig.me}"

    print_info "Testing proxy connection on ${host}:${port}..."

    # Check if curl is installed
    if ! command_exists curl; then
        print_error "curl is not installed. Please install curl to test connections."
        exit 1
    fi

    # Try to get IP through proxy with timeout
    local result=$(curl -s -m 10 -o /dev/null -w "%{http_code}" -x "${host}:${port}" "$url")
    local exit_code=$?

    if [ $exit_code -ne 0 ] || [ "$result" != "200" ]; then
        print_error "Failed to connect to proxy at ${host}:${port}"
        echo "HTTP Status: $result, Exit code: $exit_code"
        return 1
    fi

    # Get the IP address
    local ip=$(curl -s -m 10 -x "${host}:${port}" "$url")

    print_success "Proxy test successful!"
    echo
    echo "Your IP through the VPN: $ip"
    echo "Proxy connection: ${host}:${port}"
    return 0
}

# Monitor container health and restart if needed
monitor_containers() {
    local interval="${1:-$HEALTH_CHECK_INTERVAL}"

    print_info "Starting container health monitoring (Ctrl+C to stop)..."
    print_info "Checking containers every ${interval} seconds"

    while true; do
        # Get all running VPN containers
        local containers=$(docker ps --filter "label=${PREFIX}.type=vpn" --format "{{.Names}}")

        if [ -z "$containers" ]; then
            print_info "No running VPN containers found. Waiting..."
            sleep "$interval"
            continue
        fi

        echo "$(date +"%Y-%m-%d %H:%M:%S") - Checking container health..."

        for container in $containers; do
            local short_name=$(basename "$container")
            if [[ "$short_name" == "${PREFIX}_"* ]]; then
                short_name=$(echo "$short_name" | sed "s/^${PREFIX}_//")
            fi

            local port=$(docker inspect --format='{{index .Config.Labels "'${PREFIX}'.port"}}' "$container" 2>/dev/null)

            if [ -z "$port" ]; then
                print_info "Container $short_name has no port label, skipping check"
                continue
            fi

            # Test the proxy connection
            echo -n "Testing $short_name (port $port): "

            if test_proxy "$port" "localhost" "https://ifconfig.me" >/dev/null 2>&1; then
                echo -e "${GREEN}OK${NC}"
            else
                echo -e "${RED}FAILED${NC}"
                print_info "Container $short_name is not responding, restarting..."

                # Only restart if configured to do so
                if jq -e '.auto_restart_containers // true' "$CONFIG_FILE" | grep -q "true"; then
                    docker restart "$container" >/dev/null
                    print_info "Container $short_name restarted. Waiting 15s for it to initialize..."
                    sleep 15

                    # Test again after restart
                    if test_proxy "$port" "localhost" "https://ifconfig.me" >/dev/null 2>&1; then
                        print_success "Container $short_name is now working"
                    else
                        print_error "Container $short_name still not responding after restart"
                    fi
                else
                    print_info "Auto-restart is disabled. Use '$0 restart $short_name' to restart manually."
                fi
            fi
        done

        echo "Health check complete. Next check in ${interval} seconds."
        sleep "$interval"
    done
}

# ======================================================
# Batch Operations
# ======================================================

# Create batch of VPN containers from a json file
create_batch() {
    local batch_file="$1"

    if [ -z "$batch_file" ]; then
        batch_file="${SCRIPT_DIR}/vpn_batch.json"
    fi

    if [ ! -f "$batch_file" ]; then
        print_error "Batch file not found: $batch_file"
        exit 1
    fi

    # Check if jq exists
    if ! command_exists jq; then
        print_error "jq is required for batch operations but not installed."
        exit 1
    fi

    print_header "Creating containers from batch file: $batch_file"
    echo

    # Process batch file to create multiple containers
    jq -r 'keys[]' "$batch_file" | while read -r name; do
        # Extract settings
        local container_name=$(jq -r --arg name "$name" '.[$name].container_name // $name' "$batch_file")
        local port=$(jq -r --arg name "$name" '.[$name].port' "$batch_file")
        local profile=$(jq -r --arg name "$name" '.[$name].user_profile // ""' "$batch_file")
        local provider=$(jq -r --arg name "$name" '.[$name].vpn_provider // "protonvpn"' "$batch_file")
        local server_city=$(jq -r --arg name "$name" '.[$name].server_city // ""' "$batch_file")
        local server_hostname=$(jq -r --arg name "$name" '.[$name].server_hostname // ""' "$batch_file")

        print_info "Creating container: $container_name (port: $port)"

        if [ -n "$profile" ] && [ -f "${PROFILES_DIR}/${profile}.env" ]; then
            # Create container with profile
            create_vpn_from_profile "$container_name" "$port" "$profile" "$server_city" "$server_hostname" "$provider" >/dev/null 2>&1
        else
            # Fall back to regular create if profile doesn't exist
            local username=$(jq -r --arg name "$name" '.[$name].username // ""' "$batch_file")
            local password=$(jq -r --arg name "$name" '.[$name].password // ""' "$batch_file")
            local location="$server_city"
            if [ -z "$location" ]; then
                location="$server_hostname"
            fi

            create_vpn "$container_name" "$port" "$provider" "$location" "$username" "$password" >/dev/null 2>&1
        fi

        local exit_code=$?
        if [ $exit_code -eq 0 ]; then
            echo -e "  ${GREEN}✓${NC} $container_name created successfully"
        else
            echo -e "  ${RED}✗${NC} Failed to create $container_name"
        fi
    done

    print_success "Batch creation complete"
    print_info "Use '$0 list' to see all containers"
}

# Import containers from Docker Compose file
import_from_compose() {
    local compose_file="$1"

    if [ -z "$compose_file" ]; then
        print_error "Missing compose file path."
        echo "Usage: $0 import-compose <compose_file_path>"
        exit 1
    fi

    if [ ! -f "$compose_file" ]; then
        print_error "Compose file not found: $compose_file"
        exit 1
    fi

    print_header "Importing VPN containers from Docker Compose file: $compose_file"
    echo

    # Create a temporary batch file
    local batch_file="/tmp/vpn_batch_$$.json"
    echo "{" > "$batch_file"

    # Simple parsing of docker-compose file for VPN services
    local first=true
    grep -A 20 "^  vpn[0-9]*:" "$compose_file" | while read -r line; do
        if [[ "$line" =~ ^[[:space:]]*vpn([0-9]+): ]]; then
            # New container found
            if [ "$first" = "true" ]; then
                first=false
            else
                echo "  }," >> "$batch_file"
            fi

            local container_id="${BASH_REMATCH[1]}"
            local container_name="vpn${container_id}"
            echo "  \"$container_name\": {" >> "$batch_file"
            echo "    \"container_name\": \"$container_name\"," >> "$batch_file"

            # Extract port from the service block
            local port=$(grep -A 5 "^  $container_name:" "$compose_file" | grep -o "[0-9]\+:[0-9]\+" | cut -d':' -f1)
            echo "    \"port\": $port," >> "$batch_file"

            # See which profile is used
            if grep -A 1 "^  $container_name:" "$compose_file" | grep -q "<<: \*vpn-base-serg"; then
                echo "    \"user_profile\": \"serg\"," >> "$batch_file"
            elif grep -A 1 "^  $container_name:" "$compose_file" | grep -q "<<: \*vpn-base-vlad"; then
                echo "    \"user_profile\": \"vlad\"," >> "$batch_file"
            elif grep -A 1 "^  $container_name:" "$compose_file" | grep -q "<<: \*vpn-base-andr"; then
                echo "    \"user_profile\": \"andr\"," >> "$batch_file"
            fi

            # Extract SERVER_CITIES or SERVER_HOSTNAMES
            local server_city=$(grep -A 10 "^  $container_name:" "$compose_file" | grep "SERVER_CITIES" | cut -d'=' -f2- | tr -d ' ' | tr -d '"' | tr -d "'")
            local server_hostname=$(grep -A 10 "^  $container_name:" "$compose_file" | grep "SERVER_HOSTNAMES" | cut -d'=' -f2- | tr -d ' ' | tr -d '"' | tr -d "'")

            if [ -n "$server_city" ]; then
                echo "    \"server_city\": \"$server_city\"," >> "$batch_file"
            fi

            if [ -n "$server_hostname" ]; then
                echo "    \"server_hostname\": \"$server_hostname\"," >> "$batch_file"
            fi

            echo "    \"vpn_provider\": \"protonvpn\"" >> "$batch_file"
        fi
    done

    # Close the last container and the JSON object
    echo "  }" >> "$batch_file"
    echo "}" >> "$batch_file"

    # Create the containers from the batch file
    create_batch "$batch_file"

    # Clean up temporary file
    rm -f "$batch_file"
}

# ======================================================
# Preset Management
# ======================================================

# Listing of presets
list_presets() {
    if [ ! -f "$PRESETS_FILE" ]; then
        print_error "Presets file not found: $PRESETS_FILE"
        exit 1
    fi

    print_header "Available Presets:"
    echo

    # Check if jq exists
    if ! command_exists jq; then
        print_error "jq is required for preset functionality but not installed."
        exit 1
    fi

    # Print table header
    printf "%-25s %-12s %-20s %-10s %-20s %-30s\n" "NAME" "PROVIDER" "LOCATION" "PORT" "LOCATION TYPE" "DESCRIPTION"
    printf "%-25s %-12s %-20s %-10s %-20s %-30s\n" "----" "--------" "--------" "----" "-------------" "-----------"

    # Parse the presets file
    jq -r 'keys[]' "$PRESETS_FILE" | while read -r name; do
        provider=$(jq -r --arg name "$name" '.[$name].vpn_provider' "$PRESETS_FILE")
        port=$(jq -r --arg name "$name" '.[$name].port' "$PRESETS_FILE")
        desc=$(jq -r --arg name "$name" '.[$name].description' "$PRESETS_FILE")

        # Check for different location types
        server_country=$(jq -r --arg name "$name" '.[$name].server_location // empty' "$PRESETS_FILE")
        server_city=$(jq -r --arg name "$name" '.[$name].environment.SERVER_CITIES // empty' "$PRESETS_FILE")
        server_hostname=$(jq -r --arg name "$name" '.[$name].environment.SERVER_HOSTNAMES // empty' "$PRESETS_FILE")

        location="$server_country"
        location_type="country"

        if [ -n "$server_city" ]; then
            location="$server_city"
            location_type="city"
        elif [ -n "$server_hostname" ]; then
            location="$server_hostname"
            location_type="hostname"
        fi

        printf "%-25s %-12s %-20s %-10s %-20s %-30s\n" "$name" "$provider" "$location" "$port" "$location_type" "$desc"
    done
}

# Apply a preset with  options
apply_preset() {
    local preset_name="$1"
    local container_name="$2"

    if [ -z "$preset_name" ] || [ -z "$container_name" ]; then
        print_error "Missing preset name or container name."
        echo "Usage: $0 apply-preset <preset_name> <container_name>"
        exit 1
    fi

    if [ ! -f "$PRESETS_FILE" ]; then
        print_error "Presets file not found: $PRESETS_FILE"
        exit 1
    fi

    # Check if jq exists
    if ! command_exists jq; then
        print_error "jq is required for preset functionality but not installed."
        exit 1
    fi

    # Check if preset exists
    if ! jq -e --arg name "$preset_name" 'has($name)' "$PRESETS_FILE" | grep -q "true"; then
        print_error "Preset '${preset_name}' not found"
        exit 1
    fi

    # Extract preset data
    local provider=$(jq -r --arg name "$preset_name" '.[$name].vpn_provider' "$PRESETS_FILE")
    local port=$(jq -r --arg name "$preset_name" '.[$name].port' "$PRESETS_FILE")

    # Check for different location types in the preset
    local location=$(jq -r --arg name "$preset_name" '.[$name].server_location // ""' "$PRESETS_FILE")
    local server_city=$(jq -r --arg name "$preset_name" '.[$name].environment.SERVER_CITIES // ""' "$PRESETS_FILE")
    local server_hostname=$(jq -r --arg name "$preset_name" '.[$name].environment.SERVER_HOSTNAMES // ""' "$PRESETS_FILE")

    local location_type="country"
    local location_value="$location"

    if [ -n "$server_city" ]; then
        location_type="city"
        location_value="$server_city"
    elif [ -n "$server_hostname" ]; then
        location_type="hostname"
        location_value="$server_hostname"
    fi

    # Check if a user profile is specified
    local profile=$(jq -r --arg name "$preset_name" '.[$name].user_profile // ""' "$PRESETS_FILE")

    # Full container name - if using numeric naming, keep as is
    local full_container_name="$container_name"
    if [[ ! "$container_name" == vpn* ]]; then
        full_container_name="${PREFIX}_${container_name}"
    fi

    # Check if container already exists
    if docker ps -a --format '{{.Names}}' | grep -q "^${full_container_name}$"; then
        print_error "Container ${full_container_name} already exists"
        exit 1
    fi

    print_info "Applying preset '${preset_name}' to create container '${container_name}'..."

    # Ensure network exists
    ensure_network

    # Add device tun if configured
    if jq -e '.use_device_tun // true' "$CONFIG_FILE" | grep -q "true"; then
        device_args="--device /dev/net/tun:/dev/net/tun"
    else
        device_args=""
    fi

    # Create environment variables array
    env_vars=(
        "-e VPN_SERVICE_PROVIDER=${provider}"
        "-e HTTPPROXY=on"
        "-e HTTPPROXY_LISTENING_ADDRESS=:8888"
    )

    # Add profile env file if specified
    if [ -n "$profile" ] && [ -f "${PROFILES_DIR}/${profile}.env" ]; then
        env_vars+=("--env-file ${PROFILES_DIR}/${profile}.env")
    fi

    # Add location settings
    if [ "$location_type" = "country" ] && [ -n "$location_value" ]; then
        env_vars+=("-e SERVER_COUNTRIES=${location_value}")
    elif [ "$location_type" = "city" ] && [ -n "$location_value" ]; then
        env_vars+=("-e SERVER_CITIES=${location_value}")
    elif [ "$location_type" = "hostname" ] && [ -n "$location_value" ]; then
        env_vars+=("-e SERVER_HOSTNAMES=${location_value}")
    fi

    # Add all environment variables from the preset
    local preset_env_vars=$(jq -r --arg name "$preset_name" '.[$name].environment | to_entries[] | "-e \(.key)=\(.value)"' "$PRESETS_FILE")
    if [ -n "$preset_env_vars" ]; then
        for env_var in $preset_env_vars; do
            env_vars+=("$env_var")
        done
    fi

    # Create container using the preset
    docker run -d \
        --name "$full_container_name" \
        --restart unless-stopped \
        -p "${port}:8888" \
        --cap-add=NET_ADMIN \
        ${device_args} \
        --network "${PREFIX}_network" \
        --label "${PREFIX}.type=vpn" \
        --label "${PREFIX}.port=${port}" \
        --label "${PREFIX}.internal_port=8888" \
        --label "${PREFIX}.provider=${provider}" \
        --label "${PREFIX}.profile=${profile}" \
        --label "${PREFIX}.location_type=${location_type}" \
        --label "${PREFIX}.location=${location_value}" \
        --label "${PREFIX}.preset=${preset_name}" \
        ${env_vars[@]} \
        qmcgaw/gluetun:latest

    local exit_code=$?
    if [ $exit_code -eq 0 ]; then
        print_success "Applied preset '${preset_name}' and created container '${container_name}' on port ${port}"
        print_info "To test the connection: curl -x localhost:${port} https://ifconfig.me"
    else
        print_error "Failed to apply preset"
        exit $exit_code
    fi
}

# Create a new preset from existing container
create_preset() {
    local container_name="$1"
    local preset_name="$2"
    local description="$3"

    if [ -z "$container_name" ] || [ -z "$preset_name" ]; then
        print_error "Missing container name or preset name."
        echo "Usage: $0 create-preset <container_name> <preset_name> [description]"
        exit 1
    fi

    # Full container name - if using numeric naming, keep as is
    local full_container_name="$container_name"
    if [[ ! "$container_name" == vpn* ]]; then
        full_container_name="${PREFIX}_${container_name}"
    fi

    # Check if container exists
    if ! docker ps -a --format '{{.Names}}' | grep -q "^${full_container_name}$"; then
        print_error "Container ${full_container_name} does not exist"
        exit 1
    fi

    # Check if jq exists
    if ! command_exists jq; then
        print_error "jq is required for preset functionality but not installed."
        exit 1
    fi

    # Extract container information
    local port=$(docker inspect --format='{{index .Config.Labels "'${PREFIX}'.port"}}' "$full_container_name" 2>/dev/null)
    local provider=$(docker inspect --format='{{index .Config.Labels "'${PREFIX}'.provider"}}' "$full_container_name" 2>/dev/null)
    local profile=$(docker inspect --format='{{index .Config.Labels "'${PREFIX}'.profile" | printf "%s"}}' "$full_container_name" 2>/dev/null)
    local location_type=$(docker inspect --format='{{index .Config.Labels "'${PREFIX}'.location_type" | printf "%s"}}' "$full_container_name" 2>/dev/null)
    local location=$(docker inspect --format='{{index .Config.Labels "'${PREFIX}'.location"}}' "$full_container_name" 2>/dev/null)

    # Get all environment variables
    local env_vars=$(docker inspect --format='{{range .Config.Env}}{{.}} {{end}}' "$full_container_name")

    # Create a temporary file for the environment
    local env_file="/tmp/preset_env_$$.json"
    echo "{" > "$env_file"

    # Add environment variables
    local first=true
    for env in $env_vars; do
        # Skip empty values
        if [ -z "$env" ]; then
            continue
        fi

        # Skip PATH and HOME
        if [[ "$env" == PATH=* ]] || [[ "$env" == HOME=* ]]; then
            continue
        fi

        # Split into key and value
        local env_key=$(echo "$env" | cut -d= -f1)
        local env_value=$(echo "$env" | cut -d= -f2-)

        if [ "$first" = "true" ]; then
            first=false
        else
            echo "," >> "$env_file"
        fi

        echo "  \"$env_key\": \"$env_value\"" >> "$env_file"
    done

    echo "}" >> "$env_file"

    # Create temp file for the new preset
    local preset_file="/tmp/preset_$$.json"

    # Load existing presets or create new file
    if [ -f "$PRESETS_FILE" ]; then
        cp "$PRESETS_FILE" "$preset_file"
    else
        echo "{}" > "$preset_file"
    fi

    # Add the new preset
    jq --arg name "$preset_name" \
       --arg provider "$provider" \
       --arg location "$location" \
       --arg port "$port" \
       --arg desc "${description:-Created from container $container_name}" \
       --arg profile "$profile" \
       --arg location_type "$location_type" \
       --argjson env "$(cat "$env_file")" \
       '.[$name] = {
         "name": $name,
         "vpn_provider": $provider,
         "server_location": $location,
         "port": ($port | tonumber),
         "user_profile": $profile,
         "location_type": $location_type,
         "environment": $env,
         "description": $desc,
         "created_at": (now | todate),
         "updated_at": (now | todate)
       }' "$preset_file" > "${preset_file}.new"

    # Move the new preset file to the preset location
    if [ -f "${preset_file}.new" ]; then
        mv "${preset_file}.new" "$PRESETS_FILE"
        print_success "Created preset '$preset_name' from container '$container_name'"
    else
        print_error "Failed to create preset"
        exit 1
    fi

    # Clean up temp files
    rm -f "$env_file" "$preset_file"
}

# ======================================================
# Help and Usage Information
# ======================================================

# Show usage information
show_usage() {
    echo "Proxy2VPN - Advanced VPN Container Manager"
    echo
    echo "Usage: $0 <command> [arguments]"
    echo
    echo "Profile Commands:"
    echo "  create-profile <profile> <username> <password>"
    echo "                           Create a new user profile for VPN credentials"
    echo "  list-profiles            List all user profiles"
    echo
    echo "Container Commands:"
    echo "  create <container> <port> <provider> [location] [username] [password]"
    echo "                           Create a new VPN container"
    echo "  create-from-profile <container> <port> <profile> [server_city] [server_hostname] [provider]"
    echo "                           Create a container using a profile"
    echo "  list                     List all VPN containers"
    echo "  delete <container>       Delete a VPN container"
    echo "  update <container> <key> <value>"
    echo "                           Update container configuration"
    echo "  logs <container> [lines] View container logs"
    echo "  start <container>        Start a container"
    echo "  stop <container>         Stop a container"
    echo
    echo "Server Information Commands:"
    echo "  update-server-lists       Update server lists for all providers"
    echo "  list-countries <provider> List available countries for a provider"
    echo "  list-cities <provider> <country_code>"
    echo "                           List available cities for a country and provider"
    echo
    echo "Batch Operations:"
    echo "  create-batch [file]      Create multiple containers from a batch file"
    echo "                           (Default: ./vpn_batch.json)"
    echo "  import-compose <file>    Import containers from a Docker Compose file"
    echo
    echo "Preset Commands:"
    echo "  presets                  List all presets"
    echo "  apply-preset <preset> <container>"
    echo "                           Apply a preset to create a container"
    echo "  create-preset <container> <preset> [description]"
    echo "                           Create a preset from an existing container"
    echo
    echo "Monitoring Commands:"
    echo "  test [port] [host] [url] Test VPN proxy connection"
    echo "  monitor [interval]       Monitor all containers and auto-restart if needed"
    echo
    echo "Examples:"
    echo "  $0 create-profile myuser john.doe@example.com mysecretpass"
    echo "  $0 create-from-profile vpn1 8888 myuser \"New York\""
    echo "  $0 test 8888"
    echo "  $0 apply-preset protonvpn-us vpn2"
    echo "  $0 list-countries protonvpn"
    echo
    echo "Notes:"
    echo "  - Container names can be specified with or without the '$PREFIX' prefix"
    echo "  - When using numeric naming (vpn1, vpn2, etc.), no prefix is added"
    echo "  - Profiles are stored in ${PROFILES_DIR}/*.env files"
    echo "  - Presets are stored in $PRESETS_FILE"
    echo "  - Server lists are cached in $CACHE_DIR"
}

# ======================================================
# Main Entry Point
# ======================================================
main() {
    # Initialize environment
    init_environment

    # Check dependencies
    check_dependencies

    # No arguments, show usage
    if [ $# -eq 0 ]; then
        show_usage
        exit 1
    fi

    # Get command
    cmd="$1"
    shift

    # Process commands
    case "$cmd" in
        # Profile commands
        create-profile)
            create_profile "$@"
            ;;
        list-profiles)
            list_profiles
            ;;

        # Container commands
        create)
            create_vpn "$@"
            ;;
        create-from-profile)
            create_vpn_from_profile "$@"
            ;;
        list)
            list_containers
            ;;
        delete)
            delete_container "$@"
            ;;
        update)
            update_container "$@"
            ;;
        logs)
            view_logs "$@"
            ;;
        start)
            start_container "$@"
            ;;
        stop)
            stop_container "$@"
            ;;

        # Server Information commands
        update-server-lists)
            update_all_server_lists
            ;;
        list-countries)
            list_countries "$@"
            ;;
        list-cities)
            list_cities "$@"
            ;;

        # Batch operations
        create-batch)
            create_batch "$@"
            ;;
        import-compose)
            import_from_compose "$@"
            ;;

        # Preset commands
        presets)
            list_presets
            ;;
        apply-preset)
            apply_preset "$@"
            ;;
        create-preset)
            create_preset "$@"
            ;;

        # Monitoring commands
        test)
            test_proxy "$@"
            ;;
        monitor)
            monitor_containers "$@"
            ;;

        # Help
        *)
            print_error "Unknown command: $cmd"
            show_usage
            exit 1
            ;;
    esac
}

# Run main function
main "$@"