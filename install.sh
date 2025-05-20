#!/bin/bash
#
# proxy2vpn installer script
# This script downloads the proxy2vpn script and sets it up in the user's bin directory
# Version: 1.0.0
#

# Destination paths - define early for cleanup function
USER_BIN="${HOME}/.local/bin"
INSTALL_DIR="${HOME}/.proxy2vpn"
SCRIPT_PATH="${USER_BIN}/proxy2vpn"

# Error handling and cleanup
cleanup() {
    local error_code=$?
    if [[ "${error_code}" -ne 0 ]]; then
        echo -e "\n\033[0;31mInstallation failed with error code ${error_code}\033[0m"

        # Only attempt cleanup if directories were created
        if [[ -d "${INSTALL_DIR}" ]] || [[ -f "${SCRIPT_PATH}" ]]; then
            echo -e "\033[1;33mCleaning up partially installed files...\033[0m"

            # Remove created files
            [[ -f "${SCRIPT_PATH}" ]] && rm -f "${SCRIPT_PATH}"
            [[ -d "${INSTALL_DIR}" ]] && rm -rf "${INSTALL_DIR}"

            echo -e "\033[0;32mCleanup completed.\033[0m"
    fi
  fi
    exit "${error_code}"
}

# Set up trap for script exit
trap cleanup EXIT

# Exit on error
set -e

# Function to handle Ctrl+C gracefully
ctrl_c() {
    echo -e "\n\033[0;31mInstallation canceled by user\033[0m"
    exit 130
}

# Set up trap for interrupt signal
trap ctrl_c INT

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

# Make sure curl is installed
if ! command -v curl >/dev/null 2>&1; then
    print_error "curl is not installed. Please install curl to use this script."
    print_info "Install curl with: brew install curl (macOS) or apt install curl (Ubuntu/Debian)"
    exit 1
fi

# Make sure Docker is installed
if ! command -v docker >/dev/null 2>&1; then
    print_error "Docker is not installed. Please install Docker to use proxy2vpn."
    exit 1
fi

# Check for jq
if ! command -v jq >/dev/null 2>&1; then
    print_info "jq is not installed. This is required for some proxy2vpn features."
    print_info "We recommend installing jq with:"
    uname_output="$(uname)"
    if [[ "${uname_output}" == "Darwin" ]]; then
        print_info "  brew install jq   (using Homebrew on macOS)"
  elif   [[ "${uname_output}" == "Linux" ]]; then
        print_info "  apt install jq    (Debian/Ubuntu)"
        print_info "  yum install jq    (CentOS/RHEL)"
  fi

    read -p "Would you like to continue installation without jq? (y/n) " -n 1 -r
    echo
    if [[ ! ${REPLY} =~ ^[Yy]$ ]]; then
        print_error "Installation canceled. Please install jq and try again."
        exit 1
  fi
    print_info "Continuing installation without jq. Some features may be limited."
fi

# Raw URLs for GitHub files
REPO_RAW_URL="https://raw.githubusercontent.com/eirenik0/proxy2vpn/main"

# Create directories
print_info "Creating installation directories..."
mkdir -p "${USER_BIN}"
mkdir -p "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}/profiles"
mkdir -p "${INSTALL_DIR}/presets"

# Download main script
print_info "Downloading proxy2vpn script..."
curl -s "${REPO_RAW_URL}/proxy2vpn.sh" >"${INSTALL_DIR}/proxy2vpn.sh.tmp"

# Update paths in the script to point to the installation directory
print_info "Adjusting script paths..."
sed "s|SCRIPT_DIR=\"\$(dirname \"\$(readlink -f \"\$0\")\")\"|\
SCRIPT_DIR=\"${INSTALL_DIR}\"|g" "${INSTALL_DIR}/proxy2vpn.sh.tmp" >"${INSTALL_DIR}/proxy2vpn.sh"

rm "${INSTALL_DIR}/proxy2vpn.sh.tmp"
chmod +x "${INSTALL_DIR}/proxy2vpn.sh"

# Download default config
print_info "Setting up default configuration..."
curl -s "${REPO_RAW_URL}/config.json" >"${INSTALL_DIR}/config.json"

# Download presets
print_info "Setting up presets..."
curl -s "${REPO_RAW_URL}/presets/presets.json" >"${INSTALL_DIR}/presets/presets.json"

# Create profiles README
cat >"${INSTALL_DIR}/profiles/README.md"  <<EOF
# User Profiles

This directory contains user profiles for proxy2vpn.
Each profile is stored as an .env file with VPN credentials.

To create a profile, use:
\`\`\`bash
proxy2vpn create-profile myprofile username password
\`\`\`
EOF

# Create wrapper script in user bin
cat >"${SCRIPT_PATH}"  <<EOF
#!/bin/bash
# proxy2vpn wrapper script
exec "${INSTALL_DIR}/proxy2vpn.sh" "\$@"
EOF

chmod +x "${SCRIPT_PATH}"

# Check if bin directory is in PATH
if [[ ":${PATH}:" != *":${USER_BIN}:"* ]]; then
    print_info "Adding ${USER_BIN} to your PATH..."

    # Determine shell configuration file
    SHELL_CONFIG=""
    if [[ "${SHELL}" == *"bash"* ]]; then
        SHELL_CONFIG="${HOME}/.bashrc"
  elif   [[ "${SHELL}" == *"zsh"* ]]; then
        SHELL_CONFIG="${HOME}/.zshrc"
  fi

    if [[ -n "${SHELL_CONFIG}" ]]; then
        echo "export PATH=\"${HOME}/.local/bin:${PATH}\"" >>"${SHELL_CONFIG}"
        print_info "Added ${USER_BIN} to ${SHELL_CONFIG}"
        print_info "Please restart your terminal or run 'source ${SHELL_CONFIG}' to update your PATH"
  else
        print_info "Please add ${USER_BIN} to your PATH manually"
  fi
fi

# Verify installation
print_info "Verifying installation..."
verify_installation() {
    local issues=0

    # Check if main script exists
    if [[ ! -f "${INSTALL_DIR}/proxy2vpn.sh" ]]; then
        print_error "Main script not found at ${INSTALL_DIR}/proxy2vpn.sh"
        issues=$((issues + 1))
  fi

    # Check if wrapper exists
    if [[ ! -f "${SCRIPT_PATH}" ]]; then
        print_error "Wrapper script not found at ${SCRIPT_PATH}"
        issues=$((issues + 1))
  fi

    # Check if wrapper is executable
    if [[ -f "${SCRIPT_PATH}" && ! -x "${SCRIPT_PATH}" ]]; then
        print_error "Wrapper script is not executable"
        issues=$((issues + 1))
  fi

    # Check if config exists
    if [[ ! -f "${INSTALL_DIR}/config.json" ]]; then
        print_error "Configuration file not found at ${INSTALL_DIR}/config.json"
        issues=$((issues + 1))
  fi

    # Check if presets folder exists
    if [[ ! -d "${INSTALL_DIR}/presets" ]]; then
        print_error "Presets directory not found at ${INSTALL_DIR}/presets"
        issues=$((issues + 1))
  fi

    # Check if profiles folder exists
    if [[ ! -d "${INSTALL_DIR}/profiles" ]]; then
        print_error "Profiles directory not found at ${INSTALL_DIR}/profiles"
        issues=$((issues + 1))
  fi

    # Return number of issues found
    return "${issues}"
}

verify_result=0
verify_installation
verify_result=$?
if [[ ${verify_result} -eq 0 ]]; then
    print_success "proxy2vpn has been installed successfully!"
    print_info "You can now use the 'proxy2vpn' command from anywhere."
    print_info "Try running 'proxy2vpn' to see available commands."
    print_info "Configuration files are stored in ${INSTALL_DIR}"

    # Try to run the script for basic validation
    if "${INSTALL_DIR}/proxy2vpn.sh" >/dev/null 2>&1; then
        print_success "Basic script validation successful."
  else
        print_error "Basic script validation failed. There might be issues with the installation."
        print_info "You can manually check the script with: ${INSTALL_DIR}/proxy2vpn.sh"
  fi
else
    print_error "Installation completed with some issues. Please check the errors above."
    print_info "You may need to fix these issues manually or reinstall."
fi
