# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Proxy2VPN is a Python command-line interface for managing multiple VPN containers with Docker. It supports user profiles, health monitoring, batch operations, and dynamic server list fetching using the qmcgaw/gluetun Docker image.

## Development Commands

### Building and Testing
```bash
# Install dependencies
uv sync

# Run Python application
uv run proxy2vpn <command> [args]

# Run tests (if available)
pytest

# Development installation
pip install -e .
```

### Running the Application
```bash
# Run using uv
uv run proxy2vpn <command> [args]

# Common commands
uv run proxy2vpn profile create myprofile profiles/myprofile.env
uv run proxy2vpn vpn create vpn1 myprofile --port 8888 --provider protonvpn
uv run proxy2vpn vpn start vpn1
uv run proxy2vpn vpn list --diagnose
uv run proxy2vpn vpn start --all
uv run proxy2vpn servers list-providers
uv run proxy2vpn vpn test vpn1
uv run proxy2vpn system diagnose --verbose
```

## High-Level Architecture

### Application Organization
The Python application (`src/proxy2vpn/`) is modular with these components:
1. **cli.py**: Main CLI interface using Typer with command groups (profile, vpn, servers, system, preset)
2. **config.py**: Configuration constants and defaults (compose file paths, cache dir, default provider)
3. **models.py**: Data models for VPNService and Profile with compose file serialization
4. **compose_manager.py**: Docker Compose file management using ruamel.yaml for profiles and services
5. **docker_ops.py**: Docker container operations using docker-py SDK
6. **server_manager.py**: Gluetun server list fetching, caching, and location validation
7. **preset_manager.py**: Preset management built on YAML anchors
8. **compose_utils.py**: Docker Compose utilities
9. **typer_ext.py**: Typer extensions for enhanced CLI functionality
10. **diagnostics.py**: Container log analysis and health scoring system

### Docker Integration
All VPN containers use the `qmcgaw/gluetun` image with:
- Automatic network creation (proxy2vpn_network)
- Consistent labeling (type=proxy2vpn)
- Environment variable injection for VPN configuration
- HTTP proxy authentication support

### Configuration System
- **compose.yml**: Single source of truth for all state (services, profiles as YAML anchors)
- **profiles/*.env**: VPN credentials (OPENVPN_USER, OPENVPN_PASSWORD) referenced by profiles
- **~/.cache/proxy2vpn/**: Server list cache with TTL management
- **pyproject.toml**: Project configuration, dependencies, and towncrier settings

### Testing & Diagnostics Architecture
The application includes:
- Direct pytest integration support
- Docker container lifecycle operations
- VPN connection testing via proxy validation
- Server list validation against gluetun's official data
- Container health monitoring with diagnostic log analysis
- Automated troubleshooting recommendations for common VPN issues

## Version Management

The current version is tracked in `pyproject.toml`. When making changes:
1. Update version in `pyproject.toml`
2. Update CHANGELOG.md using towncrier
3. Run `make all` to ensure everything passes

## Key Implementation Details

### Error Handling
- Python exception handling with Typer error reporting
- Docker API error handling (NotFound, APIError) with user-friendly messages
- Graceful degradation for network failures and cache misses
- SSL certificate validation with bypass option for troubleshooting

### Key Dependencies
Core dependencies managed in pyproject.toml:
- **typer**: CLI framework with command groups and rich help
- **docker**: Docker SDK for Python container operations
- **ruamel.yaml**: YAML processing with anchor/merge support for compose files
- **requests**: HTTP client for server list fetching and VPN testing
- **towncrier**: Changelog management (dev dependency)

### Server List Integration
ServerManager class handles:
- Fetching from gluetun's official servers.json on GitHub
- Local caching in ~/.cache/proxy2vpn/ with 24-hour TTL
- Location validation for providers, countries, and cities
- CLI commands: list-providers, list-countries, list-cities, validate-location

### Container Management
Containers are managed through:
- Docker Compose YAML files with YAML anchors for profiles
- Automatic port allocation starting from 20000
- Container labeling with `vpn.type=vpn` for multi-service operations
- Profile-based configuration using environment file references

## Development Guidelines

This project uses [Towncrier](https://towncrier.readthedocs.io/) to manage the changelog.

## News fragments

- For every pull request, add a file under `news/` named `<PR_NUMBER>.<type>.md`.
- Supported fragment types:
  - `feature` – new features
  - `bugfix` – bug fixes
  - `doc` – documentation updates
  - `removal` – deprecated feature removals
  - `misc` – other changes
- Each fragment must contain a one-line description.

## Changelog

- Run `make changelog-draft` to preview upcoming release notes.
- Run `make changelog VERSION=x.y.z` to finalize the changelog for a release.
- Fragments are removed automatically when the changelog is built.

- Run `make fmt` and `make lint` after code change
- After implementing feature we have to update `/news` folder respectively
