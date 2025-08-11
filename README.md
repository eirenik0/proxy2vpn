# Proxy2VPN

Python command-line interface for managing multiple VPN containers with Docker.

## Features
- Manage VPN credentials as reusable profiles
- Create and control VPN services
- Bulk start/stop operations
- Query and validate provider server locations
- Apply predefined presets for common setups

## Installation

### Quick install
```bash
curl -s https://raw.githubusercontent.com/eirenik0/proxy2vpn/main/install.py | python3 -
```
This installs the `proxy2vpn` CLI to `~/.local/bin` (ensure it's on your `PATH`).

### Manual install
```bash
git clone https://github.com/eirenik0/proxy2vpn.git
cd proxy2vpn
pip install .
```

## Quick Start
1. Create a profile file with your VPN credentials:
   ```bash
   mkdir -p profiles
   cat <<'EOF2' > profiles/myprofile.env
   OPENVPN_USER=username
   OPENVPN_PASSWORD=password
   EOF2
   ```

2. Register the profile with proxy2vpn:
   ```bash
   proxy2vpn profile create myprofile profiles/myprofile.env
   ```

3. Create and start a VPN service:
   ```bash
   proxy2vpn vpn create vpn1 myprofile --port 8888 --provider protonvpn --location "New York"
   proxy2vpn vpn start vpn1
   ```

4. View status and test connectivity:
   ```bash
   proxy2vpn vpn list
   proxy2vpn test vpn1
   ```

## Command overview

### Profiles
- `proxy2vpn profile create NAME ENV_FILE`
- `proxy2vpn profile list`
- `proxy2vpn profile delete NAME`

### VPN services
- `proxy2vpn vpn create NAME PROFILE [--port PORT] [--provider PROVIDER] [--location LOCATION]`
- `proxy2vpn vpn list`
- `proxy2vpn vpn start NAME`
- `proxy2vpn vpn stop NAME`
- `proxy2vpn vpn restart NAME`
- `proxy2vpn vpn logs NAME [--lines N] [--follow]`
- `proxy2vpn vpn delete NAME`

### Bulk operations
- `proxy2vpn bulk up`
- `proxy2vpn bulk down`
- `proxy2vpn bulk status`
- `proxy2vpn bulk ips`

### Server database
- `proxy2vpn servers update`
- `proxy2vpn servers list-providers`
- `proxy2vpn servers list-countries PROVIDER`
- `proxy2vpn servers list-cities PROVIDER COUNTRY`
- `proxy2vpn servers validate-location PROVIDER LOCATION`

### Presets
- `proxy2vpn preset list`
- `proxy2vpn preset apply PRESET SERVICE [--port PORT]`

### Testing
- `proxy2vpn test SERVICE` – verify that a proxy container is reachable

## Development
Run tests with:
```bash
pytest
```

## License
MIT
