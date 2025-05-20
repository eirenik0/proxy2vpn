# Proxy2VPN

An advanced bash script to manage multiple VPN containers using Docker.

## Overview

This tool simplifies the management of multiple VPN containers, each running their own proxy server using the [gluetun](https://github.com/qdm12/gluetun) image. You can create, configure, and monitor VPN proxies for different locations with minimal effort.

## Features

- **User Profiles**: Store VPN credentials securely in profile files
- **Batch Operations**: Create multiple containers from a single command
- **Connection Health Monitoring**: Auto-restart non-responsive containers
- **Preset System**: Support for cities and specific server hostnames
- **Docker Compose Import**: Import your existing Docker Compose setup
- **Docker Compose-like Commands**: Use `up` and `down` commands to start/stop containers
- **Granular OpenVPN Settings**: Configure specific server hostnames and cities
- **Dynamic Server Lists**: Fetch and use server information directly from gluetun
- **HTTP Proxy Authentication**: Set username and password for HTTP proxy access

## Installation

### Option 1: Quick Install (Recommended)

Install directly using curl:

```bash
curl -s https://raw.githubusercontent.com/eirenik0/proxy2vpn/main/install.sh | bash
```

This will:
- Download the script to `~/.proxy2vpn/`
- Create a wrapper in `~/.local/bin/proxy2vpn`
- Set up the required directory structure
- Add the bin directory to your PATH (if needed)

### Uninstalling

To remove proxy2vpn from your system:

```bash
curl -s https://raw.githubusercontent.com/eirenik0/proxy2vpn/main/uninstall.sh | bash
```

This will remove the proxy2vpn script and all configuration files but will leave any Docker containers you've created intact.

### Option 2: Manual Installation

1. Clone the repository:
   ```bash
   git clone https://eirenik0/proxy2vpn.git
   cd proxy2vpn
   ```

2. Make the script executable:
   ```bash
   chmod +x proxy2vpn.sh
   ```

## Quick Start

1. Create a user profile:
   ```bash
   ./proxy2vpn.sh create-profile myprofile username password
   ```

2. Create a VPN container:
   ```bash
   ./proxy2vpn.sh create-from-profile vpn1 8888 myprofile "New York"
   ```

3. Test the connection:
   ```bash
   ./proxy2vpn.sh test 8888
   ```

4. List your containers:
   ```bash
   ./proxy2vpn.sh list
   ```

## Batch Operations

Create multiple containers at once using a JSON batch file:

```bash
./proxy2vpn.sh create-batch vpn_batch_example.json
```

Start or stop all VPN containers at once:

```bash
# Start all VPN containers
./proxy2vpn.sh up

# Stop all VPN containers
./proxy2vpn.sh down

# Remove all VPN containers 
./proxy2vpn.sh cleanup

# Start a specific container
./proxy2vpn.sh start vpn1

# Stop a specific container
./proxy2vpn.sh stop vpn1
```

Example batch file with HTTP proxy authentication:

```json
{
  "vpn1": {
    "container_name": "vpn1",
    "port": 8888,
    "user_profile": "myprofile",
    "server_city": "New York",
    "vpn_provider": "protonvpn"
  },
  "authenticated_proxy": {
    "container_name": "vpn2",
    "port": 8889,
    "user_profile": "myprofile",
    "server_city": "Chicago",
    "vpn_provider": "protonvpn",
    "environment": {
      "HTTPPROXY_USER": "proxyuser",
      "HTTPPROXY_PASSWORD": "proxypassword"
    }
  }
}
```

Or import directly from Docker Compose:

```bash
./proxy2vpn.sh import-compose compose.yml
```

## Monitoring

Start the monitoring service to automatically check and restart containers:

```bash
./proxy2vpn.sh monitor
```

## Commands

### Profile Management
- `create-profile <profile> <username> <password>`: Create a new user profile
- `list-profiles`: List all user profiles

### Container Management
- `create-from-profile <container> <port> <profile> [server_city] [server_hostname]`: Create container with a profile
- `list`: List all VPN containers
- `delete <container>`: Delete a container
- `update <container> <key> <value>`: Update container configuration
- `logs <container> [lines]`: View container logs
- `start/stop <container>`: Start or stop a container

You can set HTTP proxy authentication by using the environment variables `HTTPPROXY_USER` and `HTTPPROXY_PASSWORD` when creating containers.

### Preset Commands
- `presets`: List all presets
- `apply-preset <preset> <container>`: Apply a preset to create a container
- `create-preset <container> <preset>`: Create a preset from existing container

### Server Information Commands
- `update-server-lists`: Fetches and caches all server information
- `list-countries <provider>`: Shows available countries for a provider
- `list-cities <provider> <country_code>`: Shows available cities in a country

### Bulk Operations
- `up`: Start all VPN containers
- `down`: Stop all VPN containers
- `cleanup`: Remove all VPN containers

### Other Commands
- `test [port] [host] [url]`: Test a proxy connection
- `monitor [interval]`: Monitor all containers for health
- `version`: Display script version information

## Configuration

The script creates a configuration file at `config.json` on first run. Edit this file to customize:

- Default VPN provider
- Container naming convention
- Health check intervals
- Auto-restart behavior
- TUN device usage
- Server cache TTL (default: 86400 seconds/24 hours)
- Server location validation (enabled by default)
- HTTP proxy authentication settings (username and password)

## Dynamic Server Lists

Proxy2VPN uses the official gluetun server list from:
```
https://raw.githubusercontent.com/qdm12/gluetun/master/internal/storage/servers.json
```

### Key Features

1. **Real-time Server Information**: 
   - Dynamically fetches and caches the most up-to-date server information from gluetun's repository
   - Ensures you always have access to the latest available servers for each provider

2. **Automatic Caching**:
   - Server information is cached locally to improve performance
   - Cache is automatically refreshed after the configured TTL (default: 24 hours)
   - All cache files are stored in the `/cache` directory

3. **Location Validation**:
   - Validates country and city names against the official server list
   - Provides helpful suggestions when location names don't match expected values
   - Can be disabled via configuration if needed

### Using Server Information

#### Viewing Available Locations

```bash
# Update all server lists to the latest version
./proxy2vpn.sh update-server-lists

# List all available countries for a provider
./proxy2vpn.sh list-countries protonvpn

# List all available cities in a country
./proxy2vpn.sh list-cities protonvpn US
```

#### Creating VPN Containers with Validation

When creating containers, server locations are automatically validated against the official server list:

```bash
# Create a container with city validation
./proxy2vpn.sh create-from-profile vpn1 8888 myprofile "New York" "" protonvpn

# Create a container by country
./proxy2vpn.sh create vpn2 8889 mullvad "United States"

# Create a container with HTTP proxy authentication
HTTPPROXY_USER=myuser HTTPPROXY_PASSWORD=mypassword ./proxy2vpn.sh create vpn3 8890 protonvpn
```

### Benefits of Dynamic Server Lists

1. **Accuracy**: Always use the correct server names as defined by the VPN provider
2. **Completeness**: Access to all available server locations
3. **Maintainability**: No need to manually update server lists when providers change their infrastructure
4. **Discovery**: Easily view all available options without searching provider websites

## Supported VPN Providers

The script supports all VPN providers available in gluetun, including:

- Cyberghost
- ExpressVPN
- HideMyAss
- Mullvad
- NordVPN
- Perfect Privacy
- Private Internet Access (PIA)
- PrivateVPN
- ProtonVPN
- PureVPN
- Surfshark
- TorGuard
- VyprVPN
- Windscribe

## Docker Compose Migration

If you're migrating from Docker Compose, use the import feature:

```bash
./proxy2vpn.sh import-compose compose.yml
```

The script will automatically create user profiles and containers based on your Docker Compose configuration.

## HTTP Proxy Authentication

Proxy2VPN supports HTTP proxy authentication to secure your proxy connections. You can enable this in several ways:

1. **Environment Variables**: Set these before running create commands
   ```bash
   HTTPPROXY_USER=myuser HTTPPROXY_PASSWORD=mypassword ./proxy2vpn.sh create vpn1 8888 protonvpn
   ```

2. **Config File**: Add these settings to `config.json`
   ```json
   {
     "httpproxy_user": "myuser",
     "httpproxy_password": "mypassword"
   }
   ```

3. **Update Existing Container**: Use the update command
   ```bash
   ./proxy2vpn.sh update vpn1 HTTPPROXY_USER myuser
   ./proxy2vpn.sh update vpn1 HTTPPROXY_PASSWORD mypassword
   ```

4. **Batch File**: Include authentication in batch operations
   ```json
   "vpn1": {
     "container_name": "vpn1",
     "port": 8888,
     "user_profile": "myprofile",
     "environment": {
       "HTTPPROXY_USER": "proxyuser",
       "HTTPPROXY_PASSWORD": "proxypassword"
     }
   }
   ```

When HTTP proxy authentication is enabled, clients must provide the username and password when connecting to the proxy.

## Development

### Shell Script Linting and Formatting

This project uses ShellCheck for linting and shfmt for formatting to maintain high-quality shell scripts. A GitHub Action automatically runs these checks on all `.sh` files when changes are pushed.

#### Running ShellCheck locally:

```bash
# Install shellcheck
# macOS
brew install shellcheck

# Ubuntu/Debian
sudo apt-get install shellcheck

# Run shellcheck on all shell scripts
shellcheck --shell=bash --enable=all --exclude=SC2317 *.sh
```

#### Using Make for development:

```bash
# Install dependencies (macOS)
brew install shellcheck shfmt

# View available tasks
make help

# Run shellcheck linting
make lint

# Check formatting without modifying files
make fmt-check

# Format all shell scripts
make fmt

# Run all checks and formatting
make all
```

The formatting options used:
- `-i 2`: Use 2 spaces for indentation
- `-ci`: Indent switch cases
- `-bn`: Place binaries like `&&` and `|` at the start of a line
- `-kp`: Keep column alignment padding

### Coding Standards

- Use `[[` instead of `[` for test expressions
- Always use braces around variable references: `${variable}` instead of `$variable`
- Double-quote variables when appropriate
- Maintain consistent indentation (2 spaces)

## License

MIT