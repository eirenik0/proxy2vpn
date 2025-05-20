#!/bin/bash
#
# proxy2vpn uninstaller script
# This script removes all proxy2vpn files from the user's system
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
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

# Destination paths
USER_BIN="${HOME}/.local/bin"
INSTALL_DIR="${HOME}/.proxy2vpn"
SCRIPT_PATH="${USER_BIN}/proxy2vpn"

print_header "proxy2vpn Uninstaller"
echo

print_info "This will remove proxy2vpn from your system."
print_info "The following files and directories will be removed:"
echo 
echo "  * ${SCRIPT_PATH}"
echo "  * ${INSTALL_DIR} (including all settings and profiles)"
echo

# Ask for confirmation
read -p "Are you sure you want to uninstall proxy2vpn? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    print_info "Uninstallation cancelled."
    exit 0
fi

# Check for running containers
if command -v docker &>/dev/null; then
    running_containers=$(docker ps --filter "label=vpn.type=vpn" --format "{{.Names}}" 2>/dev/null || echo "")
    if [ -n "$running_containers" ]; then
        print_error "There are still running VPN containers managed by proxy2vpn:"
        echo "$running_containers"
        echo
        print_info "Please stop these containers before uninstalling."
        read -p "Do you want to force uninstall anyway? (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_info "Uninstallation cancelled."
            exit 0
        fi
        print_info "Proceeding with forced uninstallation..."
    fi
fi

# Remove wrapper script
if [ -f "$SCRIPT_PATH" ]; then
    print_info "Removing wrapper script..."
    rm -f "$SCRIPT_PATH"
    print_success "Wrapper script removed."
else
    print_info "Wrapper script not found at $SCRIPT_PATH. Skipping."
fi

# Remove installation directory
if [ -d "$INSTALL_DIR" ]; then
    print_info "Removing installation directory..."
    rm -rf "$INSTALL_DIR"
    print_success "Installation directory removed."
else
    print_info "Installation directory not found at $INSTALL_DIR. Skipping."
fi

print_success "proxy2vpn has been successfully uninstalled from your system."
print_info "Note: This script did not stop or remove any Docker containers that were created by proxy2vpn."
print_info "If you want to remove those containers, you'll need to do so manually using Docker commands."