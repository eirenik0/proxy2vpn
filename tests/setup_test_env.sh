#!/bin/bash
# setup_test_env.sh - Setup test environment for proxy2vpn tests

set -e

# Script directory for relative paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "🔧 Setting up test environment..."

# Create profiles directory if it doesn't exist
if [[ ! -d "${ROOT_DIR}/profiles" ]]; then
    mkdir -p "${ROOT_DIR}/profiles"
    echo "  ↳ Created profiles directory"
fi

# Create test profiles
echo "📝 Creating test profiles..."

# Create test profile
echo "OPENVPN_USER=test_user" >"${ROOT_DIR}/profiles/test.env"
echo "OPENVPN_PASSWORD=test_password" >>"${ROOT_DIR}/profiles/test.env"
echo "VPN_SERVICE_PROVIDER=protonvpn" >>"${ROOT_DIR}/profiles/test.env"
echo "  ↳ Created test.env profile"

# Create andy profile if it doesn't exist
if [[ ! -f "${ROOT_DIR}/profiles/andy.env" ]]; then
    echo "OPENVPN_USER=andy_user" >"${ROOT_DIR}/profiles/andy.env"
    echo "OPENVPN_PASSWORD=andy_password" >>"${ROOT_DIR}/profiles/andy.env"
    echo "VPN_SERVICE_PROVIDER=protonvpn" >>"${ROOT_DIR}/profiles/andy.env"
    echo "  ↳ Created andy.env profile"
fi

# Create tom profile if it doesn't exist
if [[ ! -f "${ROOT_DIR}/profiles/tom.env" ]]; then
    echo "OPENVPN_USER=tom_user" >"${ROOT_DIR}/profiles/tom.env"
    echo "OPENVPN_PASSWORD=tom_password" >>"${ROOT_DIR}/profiles/tom.env"
    echo "VPN_SERVICE_PROVIDER=protonvpn" >>"${ROOT_DIR}/profiles/tom.env"
    echo "  ↳ Created tom.env profile"
fi

# Create kris profile if it doesn't exist
if [[ ! -f "${ROOT_DIR}/profiles/kris.env" ]]; then
    echo "OPENVPN_USER=kris_user" >"${ROOT_DIR}/profiles/kris.env"
    echo "OPENVPN_PASSWORD=kris_password" >>"${ROOT_DIR}/profiles/kris.env"
    echo "VPN_SERVICE_PROVIDER=protonvpn" >>"${ROOT_DIR}/profiles/kris.env"
    echo "  ↳ Created kris.env profile"
fi

# Create env files for Docker Compose
echo "📝 Creating env files for Docker Compose tests..."

# Create test env file
echo "OPENVPN_USER=test_user" >"${ROOT_DIR}/env.test"
echo "OPENVPN_PASSWORD=test_password" >>"${ROOT_DIR}/env.test"
echo "VPN_SERVICE_PROVIDER=protonvpn" >>"${ROOT_DIR}/env.test"
echo "  ↳ Created env.test file"

# Create sample test compose file if it doesn't exist
if [[ ! -f "${ROOT_DIR}/tests/test_compose.yml" ]]; then
    cat >"${ROOT_DIR}/tests/test_compose.yml"  <<'EOF'
x-vpn-base-test: &vpn-base-test
  image: qmcgaw/gluetun
  cap_add:
    - NET_ADMIN
  devices:
    - /dev/net/tun:/dev/net/tun
  env_file:
    - env.test

services:
  testvpn1:
    <<: *vpn-base-test
    ports:
      - "0.0.0.0:9999:8888/tcp"
    environment:
      - SERVER_CITIES=New York

  testvpn2:
    <<: *vpn-base-test
    ports:
      - "0.0.0.0:9998:8888/tcp"
    environment:
      - SERVER_CITIES=Chicago
EOF
    echo "  ↳ Created test_compose.yml file"
fi

echo "✅ Test environment setup complete!"
