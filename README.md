# Proxy2VPN

Python command-line interface for managing multiple VPN containers with Docker.

## Features
- Manage VPN credentials as reusable profiles
- Create and control VPN services
- Multi-service control with `--all` flags
- Query and validate provider server locations
- Apply predefined presets for common setups

## Installation

Install `proxy2vpn` from [PyPI](https://pypi.org/project/proxy2vpn/) using your
preferred Python tool:

### pip
```bash
pip install proxy2vpn
proxy2vpn --help
```

### uv
```bash
uv tool install proxy2vpn
proxy2vpn --help
```

### uvx
Run the CLI without installing it:
```bash
uvx proxy2vpn --help
```

### pipx
```bash
pipx install proxy2vpn
proxy2vpn --help
```

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
   EOF
   ```

3. Register the profile:
   ```bash
   proxy2vpn profile create myprofile profiles/myprofile.env
   ```

4. Create and start a VPN service:
   ```bash
   proxy2vpn vpn create vpn1 myprofile --port 8888 --provider protonvpn --location "New York"
   proxy2vpn vpn start vpn1
   # Use --force to recreate the container if it already exists
   # proxy2vpn vpn start --force vpn1
   ```

5. View status and test connectivity:
   ```bash
   proxy2vpn vpn list
   proxy2vpn vpn test vpn1
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

### VPN services
- `proxy2vpn vpn create NAME PROFILE [--port PORT] [--provider PROVIDER] [--location LOCATION]`
- `proxy2vpn vpn list [--diagnose] [--ips-only]`
- `proxy2vpn vpn start [NAME | --all] [--force]`
- `proxy2vpn vpn stop [NAME | --all]`
- `proxy2vpn vpn restart [NAME | --all] [--force]`
- `proxy2vpn vpn logs NAME [--lines N] [--follow]`
- `proxy2vpn vpn delete [NAME | --all]`
- `proxy2vpn vpn test NAME`

### Server database
- `proxy2vpn servers update`
- `proxy2vpn servers list-providers`
- `proxy2vpn servers list-countries PROVIDER`
- `proxy2vpn servers list-cities PROVIDER COUNTRY`
- `proxy2vpn servers validate-location PROVIDER LOCATION`

### Presets
- `proxy2vpn preset list`
- `proxy2vpn preset apply PRESET SERVICE [--port PORT]`

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
