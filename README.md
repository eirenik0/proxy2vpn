# Proxy2VPN

[![PyPI version](https://badge.fury.io/py/proxy2vpn.svg)](https://badge.fury.io/py/proxy2vpn)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/release/python-3100/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Enterprise-grade VPN container orchestration for developers who need reliable proxy infrastructure.**

Stop wrestling with VPN clients that crash, managing multiple accounts manually, or dealing with inconsistent proxy setups. Proxy2VPN turns Docker containers into a fleet of rock-solid VPN endpoints you can deploy, monitor, and scale across dozens of countries in minutes.

## Why Proxy2VPN?

**The Problem**: You need reliable proxy infrastructure for testing, scraping, or accessing geo-restricted content. Traditional VPN clients are unreliable, managing multiple accounts is painful, and scaling across regions is a nightmare.

**The Solution**: Containerized VPN services that just work. Deploy 50 VPN endpoints across 20 countries with a single command. Load-balance across multiple accounts automatically. Monitor health and rotate failed servers without intervention.

**Real-world use cases**:
- Web scraping with rotating IP addresses across multiple countries
- Testing geo-restricted applications from different regions  
- Load balancing traffic across multiple VPN accounts
- Creating development environments that mirror production geography
- Building resilient proxy infrastructure for CI/CD pipelines

## Key Features

- **Fleet Management**: Deploy VPN containers across multiple countries and cities in parallel
- **Profile-based Credentials**: Manage multiple VPN accounts as reusable configurations
- **Intelligent Load Balancing**: Distribute connections across accounts automatically
- **Health Monitoring**: Auto-rotate failed servers and maintain uptime
- **HTTP Proxy Support**: Built-in authenticated proxy endpoints for each VPN
- **Provider Agnostic**: Works with ProtonVPN, NordVPN, ExpressVPN, and 30+ providers via gluetun

## Requirements

- Docker and Docker Compose
- Python 3.10+
- A VPN account from any [supported provider](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers)

## Quick Installation

```bash
# Install via uvx (recommended - no global dependencies)
uvx proxy2vpn --help

# Or install globally
pip install proxy2vpn
```

> **Note**: `uvx` is part of the [uv](https://github.com/astral-sh/uv) toolchain. Install with: `curl -LsSf https://astral.sh/uv/install.sh | sh`

## 5-Minute Quick Start

Get a VPN endpoint running in under 5 minutes:

```bash
# 1. Initialize your workspace
proxy2vpn system init

# 2. Create your first profile with VPN credentials (all fields required)
mkdir -p profiles
cat <<'EOF' > profiles/production.env
VPN_TYPE=openvpn
VPN_SERVICE_PROVIDER=protonvpn
OPENVPN_USER=your_protonvpn_username
OPENVPN_PASSWORD=your_protonvpn_password
HTTPPROXY=on
HTTPPROXY_USER=proxy_user
HTTPPROXY_PASSWORD=proxy_pass
EOF

# 3. Register the profile and define a VPN service
# Option A: Add the env file you just created
proxy2vpn profile add production profiles/production.env

# Option B: Create the env file interactively (no manual file needed)
# proxy2vpn profile create production

# Add a VPN service interactively (choose name/profile/ports when prompted)
proxy2vpn vpn add --interactive

# 4. Start and test your VPN
proxy2vpn vpn start london-proxy
proxy2vpn vpn test london-proxy

# 5. Use your proxy (HTTP proxy now available on localhost:8888)
curl --proxy http://proxy_user:proxy_pass@localhost:8888 https://httpbin.org/ip
```

**That's it!** Your VPN container is running and you have an authenticated HTTP proxy endpoint.

## Quickstarts by Use Case (USA)

These short flows cover common US-focused setups. Location names must be valid for your provider (use `proxy2vpn servers list-cities <provider> "United States"` to explore).

### Single US Proxy (Local Dev)

```bash
# 1) Create a profile
proxy2vpn profile create us-dev

# 2) Define a service interactively (choose profile, ports, and location)
proxy2vpn vpn add --interactive
# Suggested answers:
# - Service name: us-nyc
# - Profile: us-dev
# - Host port: 8888 (or 0 for auto)
# - Control port: 0 (auto)
# - Location: "New York, United States"

# 3) Start and test
proxy2vpn vpn start us-nyc
proxy2vpn vpn public-ip us-nyc
curl --proxy http://user:pass@localhost:8888 https://httpbin.org/ip
```

### East/West Geo Testing (US)

```bash
# Create two services interactively
proxy2vpn vpn add --interactive
# - Service name: us-east
# - Profile: us-dev
# - Host port: 20001 (or auto)
# - Control port: 0 (auto)
# - Location: "New York, United States"

proxy2vpn vpn add --interactive
# - Service name: us-west
# - Profile: us-dev
# - Host port: 20002 (or auto)
# - Control port: 0 (auto)
# - Location: "Los Angeles, United States"

# Start and compare
proxy2vpn vpn start --all
proxy2vpn vpn list
```

### US Scraping Fleet (Multiple Endpoints)

```bash
# Plan a small fleet in the US (10 endpoints using a single profile)
proxy2vpn fleet plan --countries "United States" --profiles "us-dev:10" --unique-ips

# Deploy in parallel and check status
proxy2vpn fleet deploy --parallel
proxy2vpn fleet status --show-allocation
```

### CI: Ephemeral US Proxies

```bash
# Generate a reproducible plan file for CI
proxy2vpn fleet plan --countries "United States" --profiles "ci:3" --output ci-us-fleet.yml

# Validate or dry-run during pipeline
proxy2vpn fleet deploy --plan-file ci-us-fleet.yml --dry-run

# Deploy only when needed, then tear down
proxy2vpn fleet deploy --plan-file ci-us-fleet.yml --parallel --validate-first
proxy2vpn fleet scale down --factor 0
```

## Container Management & Monitoring

Each VPN container exposes both HTTP proxy endpoints and control APIs for programmatic management:

```bash
# Check VPN status and public IP
proxy2vpn vpn status london-proxy
proxy2vpn vpn public-ip london-proxy

# Monitor logs and restart tunnels
proxy2vpn vpn logs london-proxy --follow
proxy2vpn vpn restart-tunnel london-proxy

# Bulk operations across all services
proxy2vpn vpn start --all
proxy2vpn vpn update --all
proxy2vpn vpn list
```

**Docker Integration**: All containers use consistent labeling and networking, making them easy to integrate with existing Docker workflows and monitoring tools.

### Compose-root state

`proxy2vpn` keeps generated support files next to the active compose file.
This makes the workspace portable and avoids cwd-dependent behavior.

- `proxy2vpn system init --compose-file state/compose.yml` creates `state/control-server-auth.toml`
- `proxy2vpn profile add NAME relative/path.env` stores the relative path in `compose.yml`
- runtime commands resolve profile env files and the control auth file relative to the compose file, not the shell's current working directory

### Control server authentication

`proxy2vpn system init` generates `control-server-auth.toml` next to the active compose file and mounts it into each container automatically. The generated role uses `auth = "none"` for the localhost-bound control routes that `proxy2vpn` calls, so no extra manual setup is required for the built-in control commands.

If you need stricter access control, replace that generated file with your own Gluetun auth configuration such as:

```toml
[[roles]]
name = "qbittorrent"
routes = ["GET /v1/openvpn/portforwarded"]
auth = "basic"
username = "myusername"
password = "mypassword"
```

After editing the file, run `proxy2vpn vpn update NAME` to recreate the container with the new auth configuration.

## Enterprise Fleet Management

**The real power of Proxy2VPN**: Deploy and manage dozens of VPN endpoints across the globe like infrastructure, not individual connections.

### Multi-Provider Fleet Orchestration (New!)

**Automatic provider orchestration**: Mix ExpressVPN, NordVPN, ProtonVPN in a single deployment. Each profile specifies its provider - the system coordinates everything automatically.

```bash
# Create profiles with provider information
cat <<'EOF' > profiles/expressvpn-main.env
VPN_TYPE=openvpn
VPN_SERVICE_PROVIDER=expressvpn
OPENVPN_USER=your_expressvpn_username
OPENVPN_PASSWORD=your_expressvpn_password
HTTPPROXY=on
HTTPPROXY_USER=proxy_user
HTTPPROXY_PASSWORD=proxy_pass
EOF

cat <<'EOF' > profiles/nordvpn-backup.env
VPN_TYPE=openvpn
VPN_SERVICE_PROVIDER=nordvpn
OPENVPN_USER=your_nordvpn_username
OPENVPN_PASSWORD=your_nordvpn_password
HTTPPROXY=on
HTTPPROXY_USER=proxy_user
HTTPPROXY_PASSWORD=proxy_pass
EOF

# Register profiles
proxy2vpn profile add expressvpn-main profiles/expressvpn-main.env
proxy2vpn profile add nordvpn-backup profiles/nordvpn-backup.env
proxy2vpn profile add protonvpn-fleet profiles/protonvpn-fleet.env

# Single command deploys across ALL providers automatically
proxy2vpn fleet plan \
  --countries "Germany,France,Netherlands,United Kingdom,United States" \
  --profiles "expressvpn-main:6,nordvpn-backup:4,protonvpn-fleet:8"

# Deploy multi-provider fleet in one operation
proxy2vpn fleet deploy --parallel
```

**Result**: 18 endpoints automatically distributed across 3 VPN providers, with coordinated port allocation and intelligent load balancing.

### Scenario: Global Web Scraping Infrastructure

You need maximum IP diversity for scraping across 15 countries:

```bash
# Plan deployment: 20 endpoints across multiple providers for maximum diversity
proxy2vpn fleet plan \
  --countries "Germany,France,Netherlands,United Kingdom,United States,Canada" \
  --profiles "expressvpn-main:8,nordvpn-backup:6,protonvpn-fleet:6" \
  --unique-ips

# Deploy everything in parallel (typically completes in 2-3 minutes)
proxy2vpn fleet deploy --parallel --validate-first

# Check your fleet status - shows provider distribution
proxy2vpn fleet status --show-allocation
```

**Result**: 20 HTTP proxy endpoints across 3 different VPN providers, each with unique IP addresses for maximum scraping diversity.

### Scenario: CI/CD Pipeline Testing

Your application needs testing from different geographic regions:

```bash
# Create a test fleet for your CI pipeline
proxy2vpn fleet plan \
  --countries "Germany,Singapore,United States" \
  --profiles "ci-testing:3" \
  --output ci-fleet.yaml

# Deploy only when tests run
proxy2vpn fleet deploy --plan-file ci-fleet.yaml --dry-run
```

### Automatic Health Management

Fleet management includes intelligent health monitoring:

```bash
# Monitor and rotate failed endpoints automatically
proxy2vpn fleet rotate --criteria performance

# Scale up during high-demand periods
proxy2vpn fleet scale up --countries "United States,Germany" --factor 2

# Scale down to save resources
proxy2vpn fleet scale down --factor 0.5
```

Fleet management handles the complexity so you focus on your application, not infrastructure.

## Common Use Cases

### Web Scraping at Scale
```bash
# Multiple IPs across regions to avoid rate limiting
proxy2vpn fleet plan --countries "US,UK,DE,FR,CA" --profiles "scraping:10"
proxy2vpn fleet deploy --parallel

# Use any endpoint: curl --proxy http://user:pass@localhost:20001 https://api.example.com
```

### Geo-location Testing
```bash
# Test your app from different countries
proxy2vpn vpn add --interactive
# - Service name: us-east
# - Profile: production
# - Host port: 0
# - Control port: 0
# - Location: "New York"

proxy2vpn vpn add --interactive
# - Service name: eu-west
# - Profile: production
# - Host port: 0
# - Control port: 0
# - Location: "Amsterdam"
proxy2vpn vpn start --all
```

### CI/CD Pipeline Integration
```bash
# Include in your test pipeline
proxy2vpn fleet plan --countries "Germany,Singapore" --profiles "ci:2" --output tests/fleet.yaml
proxy2vpn fleet deploy --plan-file tests/fleet.yaml --validate-first
# Run your geo-specific tests
proxy2vpn fleet scale down --factor 0  # Clean up after tests
```

### Development Environment
```bash
# Persistent development proxies
proxy2vpn vpn add --interactive
# - Service name: dev-proxy
# - Profile: dev-account
# - Host port: 8888
# - Control port: 0
# - Location: "Netherlands"
# Always available at localhost:8888 for your development
```

## Essential Commands

### System operations
- `proxy2vpn system init [--force]`
- `proxy2vpn system validate`
- `proxy2vpn system diagnose [--lines N] [--all] [--verbose] [--json]`

### Profiles
- `proxy2vpn profile create NAME` (interactive env file creator)
- `proxy2vpn profile add NAME ENV_FILE`
- `proxy2vpn profile list`
- `proxy2vpn profile remove NAME`
- `proxy2vpn profile delete NAME`

### VPN services
- `proxy2vpn vpn add NAME --profile PROFILE [--port PORT] [--control-port PORT] [--location LOCATION]`
- `proxy2vpn vpn add --interactive`
- `proxy2vpn vpn list`
- `proxy2vpn vpn start [NAME | --all]`
- `proxy2vpn vpn stop [NAME | --all]`
- `proxy2vpn vpn restart [NAME | --all]`
- `proxy2vpn vpn update [NAME | --all]`
- `proxy2vpn vpn logs NAME [--lines N] [--follow]`
- `proxy2vpn vpn delete [NAME | --all]`
- `proxy2vpn vpn test NAME`

Notes:
- `vpn list` now includes health analysis by default; `--diagnose` and `--ips-only` options were removed.
- Provider is inferred from the selected profile during `vpn add`.
- `vpn start` starts an existing container or creates it if missing.
- `vpn restart` restarts containers in place.
- `vpn update` is the explicit command that pulls, recreates, and restarts containers.

### Server database
- `proxy2vpn servers update`
- `proxy2vpn servers list-providers`
- `proxy2vpn servers list-countries PROVIDER`
- `proxy2vpn servers list-cities PROVIDER COUNTRY`
- `proxy2vpn servers validate-location PROVIDER LOCATION`

### Fleet management
- `proxy2vpn fleet plan --countries "Germany,France" --profiles "acc1:2,acc2:8" [--output PLAN_FILE] [--unique-ips]`
- `proxy2vpn fleet deploy [--plan-file PLAN_FILE] [--parallel] [--validate-first] [--dry-run] [--force]`
- `proxy2vpn fleet status [--format table|json|yaml] [--show-allocation] [--show-health]`
- `proxy2vpn fleet rotate [--country COUNTRY] [--criteria random|performance|load] [--dry-run]`
- `proxy2vpn fleet scale up|down [--countries COUNTRIES] [--factor N]`

## Development

### Setup
```bash
# Install with development dependencies
uv sync
# or
pip install -e ".[dev]"
```

### Testing
```bash
# Run the supported full suite target
make test

# Or invoke pytest the same way the Makefile does
uv run --with pytest,pytest-xdist pytest -n auto
```

### Changelog Management
This project uses [Towncrier](https://towncrier.readthedocs.io/) for changelog management:

```bash
# Add a news fragment for your changes
echo "Your feature description" > news/<PR_NUMBER>.feature.md

# Preview the changelog
make changelog-draft

# Build the changelog (maintainers)
make changelog VERSION=x.y.z
```

Recent highlights (see CHANGELOG.md for details):
- `vpn add` is the single compose-only service-definition command.
- `vpn update` is the explicit recreate-and-refresh command for VPN containers.
- Profile lifecycle split: `profile remove` (from compose) and `profile delete` (delete env file).
- Control server auth config is created during `system init`, mounted automatically, and defaults to `auth = "none"` for localhost-bound control routes.
- Default health analysis in `vpn list`; removed `--diagnose`/`--ips-only` flags.

---

## Why Proxy2VPN Works

**Infrastructure as Code**: Treat VPN endpoints like any other infrastructure - version controlled, reproducible, and scalable.

**Built for Developers**: No GUI nonsense. Pure command-line interface that integrates with your existing workflows, CI/CD pipelines, and Docker toolchain.

**Production Ready**: Used for large-scale web scraping operations, geo-distributed testing, and enterprise proxy infrastructure. Battle-tested reliability with automatic health monitoring.

**Zero Vendor Lock-in**: Works with 30+ VPN providers. Switch providers, add accounts, or migrate configurations without rewriting your setup.

**From Minutes to Milliseconds**: Stop spending hours configuring VPN clients. Get from zero to working proxy infrastructure in under 5 minutes.

**Scale When You Need**: Start with a single endpoint, scale to hundreds across dozens of countries when your requirements grow.

## Get Started Now

```bash
uvx proxy2vpn system init
uvx proxy2vpn --help
```

Join developers who've eliminated VPN configuration headaches and built reliable proxy infrastructure that just works.

## License

MIT
