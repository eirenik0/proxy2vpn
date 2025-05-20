# Enhanced Proxy2VPN

An advanced bash script to manage multiple VPN containers using Docker.

## Overview

This tool simplifies the management of multiple VPN containers, each running their own proxy server using the [gluetun](https://github.com/qdm12/gluetun) image. You can create, configure, and monitor VPN proxies for different locations with minimal effort.

## Features

- **User Profiles**: Store VPN credentials securely in profile files
- **Batch Operations**: Create multiple containers from a single command
- **Connection Health Monitoring**: Auto-restart non-responsive containers
- **Enhanced Preset System**: Support for cities and specific server hostnames
- **Docker Compose Import**: Import your existing Docker Compose setup
- **Granular OpenVPN Settings**: Configure specific server hostnames and cities

## Installation

1. Clone the repository:
   ```bash
   git clone https://yourrepo/proxy2vpn.git
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

### Preset Commands
- `presets`: List all presets
- `apply-preset <preset> <container>`: Apply a preset to create a container
- `create-preset <container> <preset>`: Create a preset from existing container

### Other Commands
- `test [port] [host] [url]`: Test a proxy connection
- `monitor [interval]`: Monitor all containers for health

## Configuration

The script creates a configuration file at `config.json` on first run. Edit this file to customize:

- Default VPN provider
- Container naming convention
- Health check intervals
- Auto-restart behavior
- TUN device usage

## Docker Compose Migration

If you're migrating from Docker Compose, use the import feature:

```bash
./proxy2vpn.sh import-compose compose.yml
```

The script will automatically create user profiles and containers based on your Docker Compose configuration.

## License

MIT
