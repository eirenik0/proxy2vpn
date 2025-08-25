# Proxy2VPN

Python command-line interface for managing multiple VPN containers with Docker.

## Features
- Manage VPN credentials as reusable profiles
- Create and control VPN services using gluetun containers
- **Fleet management**: Bulk deployment across multiple cities and profiles
- Multi-service control with `--all` flags
- Query and validate provider server locations
- HTTP proxy authentication support
- Server health monitoring and automatic rotation
- Intelligent profile allocation with load balancing

## Requirements
- Docker and Docker Compose
- Python 3.8+

## Installation

Install `proxy2vpn` from [PyPI](https://pypi.org/project/proxy2vpn/) using `uvx`:

### uvx (run without installing)
```bash
uvx proxy2vpn --help
```

> [!NOTE]
> `uvx` is part of the `uv` toolchain. If `uv` isn't installed, get it with:
> ```bash
> curl -LsSf https://astral.sh/uv/install.sh | sh
> ```

## Quick Start
1. Initialize the compose file:
   ```bash
   proxy2vpn system init
   ```

2. Create a profile file with your VPN credentials:
   ```bash
   mkdir -p profiles
   cat <<'EOF' > profiles/myprofile.env
   OPENVPN_USER=your_username
   OPENVPN_PASSWORD=your_password
   HTTPPROXY=on
   HTTPPROXY_USER=your_proxy_username
   HTTPPROXY_PASSWORD=your_proxy_password
   EOF
   ```

3. Register the profile:
   ```bash
   proxy2vpn profile create myprofile profiles/myprofile.env
   ```

4. Create and start a VPN service:
   ```bash
   proxy2vpn vpn create vpn1 myprofile --port 8888 --provider protonvpn --location "New York"
   proxy2vpn profile apply myprofile vpn1 --port 8888
   proxy2vpn vpn start vpn1
   ```

5. View status and test connectivity:
   ```bash
   proxy2vpn vpn list
   proxy2vpn vpn test vpn1
   ```

## HTTP Control Server

Each VPN container exposes an HTTP control API on port `8000` which is mapped to a unique localhost-only port (starting at `30000`) on the host for secure access.

### Sample compose snippet

```yaml
services:
  vpn1:
    image: qmcgaw/gluetun
    ports:
      - "0.0.0.0:8888:8888/tcp"    # proxy port
    labels:
      vpn.type: vpn
      vpn.port: "8888"
      vpn.profile: myprofile
```

### CLI commands

```bash
proxy2vpn vpn status vpn1
proxy2vpn vpn public-ip vpn1
proxy2vpn vpn restart-tunnel vpn1
```

### Control API client settings

The `proxy2vpn.config` module exports reusable constants for working with the
Gluetun control API:

- `DEFAULT_TIMEOUT` – request timeout in seconds (default `10`).
- `MAX_RETRIES` – maximum retry attempts for requests (default `3`).
- `VERIFY_SSL` – whether SSL certificates are verified (enabled by default).
- `CONTROL_API_ENDPOINTS` – mapping of available endpoint paths.

Import and override these values in custom clients as needed.

## Fleet Management

For enterprise-scale deployment across multiple cities and VPN accounts:

1. Create multiple profiles with different account credentials:
   ```bash
   # Create profiles for different VPN accounts
   proxy2vpn profile create account1 profiles/account1.env
   proxy2vpn profile create account2 profiles/account2.env  
   ```

2. Plan a fleet deployment across countries:
   ```bash
   # Deploy across Germany and France with 2 slots on account1, 8 on account2
   proxy2vpn fleet plan --countries "Germany,France,Netherlands" --profiles "account1:2,account2:8" --unique-ips
   ```

3. Deploy the planned fleet:
   ```bash
   proxy2vpn fleet deploy --parallel
   ```

4. Monitor and manage the fleet:
   ```bash
   # View fleet status with profile allocation
   proxy2vpn fleet status --show-allocation
   
   # Rotate failed servers automatically
   proxy2vpn fleet rotate --dry-run
   ```

## Command overview

### System operations
- `proxy2vpn system init [--force]`
- `proxy2vpn system validate`
- `proxy2vpn system diagnose [--lines N] [--all] [--verbose] [--json]`

### Profiles
- `proxy2vpn profile create NAME ENV_FILE`
- `proxy2vpn profile list`
- `proxy2vpn profile delete NAME`
- `proxy2vpn profile apply PROFILE SERVICE [--port PORT]`

### VPN services
- `proxy2vpn vpn create NAME PROFILE [--port PORT] [--provider PROVIDER] [--location LOCATION]`
- `proxy2vpn vpn list [--diagnose] [--ips-only]`
- `proxy2vpn vpn start [NAME | --all]`
- `proxy2vpn vpn stop [NAME | --all]`
- `proxy2vpn vpn restart [NAME | --all]`
- `proxy2vpn vpn logs NAME [--lines N] [--follow]`
- `proxy2vpn vpn delete [NAME | --all]`
- `proxy2vpn vpn test NAME`

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
# Run tests (if available)
pytest
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

## License
MIT
