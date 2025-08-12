# Changelog

All notable changes to Proxy2VPN will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

<!-- towncrier release notes start -->
## [0.7.0]

### Features

- Add VPN fleet management system for bulk deployment across multiple cities and profiles. New `fleet` command group supports planning, deploying, monitoring, and rotating VPN services at scale with intelligent profile allocation and server health monitoring. (#100)
- feat: rollback added services on fleet deployment failure and clean up containers.
  feat: sync fleet status with compose services and include per-country/profile counts (#102)
- Add server location validation to fleet deployment. (#103)
- Apply Rich TUI formatting to profile and VPN listings for a consistent CLI experience. (#107)
- Apply fleet-style TUI feedback across core CLI commands. (#108)
- Add comprehensive async optimizations for network operations including concurrent IP fetching via aiohttp, async VPN connection testing, async server list downloads, and async diagnostic connectivity checks. CLI commands now use `@run_async` decorator with safeguards against nested event loops, delivering 5-17x performance improvements for network-bound operations. (#109)

### Miscellaneous

- Add tests covering fleet deployment planning. (#101)

### Removals

- Removed preset commands in favor of `profile apply` for creating services from profiles. (#104)


## [0.6.0]

### Features

- feat: allow `vpn restart --all --force` to recreate containers (#1)
- feat: default VPN start, stop, and restart commands now recreate containers, removing the `--force` flag (#2)
- Add HTTP proxy authentication support for VPN containers using HTTPPROXY_USER and HTTPPROXY_PASSWORD environment variables (#3)


## [0.5.1]

### Bug fixes

- Fix external IP detection by falling back to ipinfo when ifconfig.me is unreachable. (#123)
- Allow diagnosing specific containers and compute health scores for diagnostic results. (#124)


## [0.5.0]

### Bug fixes

- Fix generated compose files to drop the deprecated `version` key and merge VPN services with their profiles via `<<: *vpn-base-<profile>`. (#145)

### Features

- Add server list update when running `system init`. (#1)


## [0.4.1]

### Bug fixes

- Fix logging field collision error and container creation workflow. The application no longer crashes with KeyError when logging container operations, and properly creates containers when they don't exist during `vpn start` command. (#0)


## [0.4.0]

### Features

- Add --force flag to `vpn start` to recreate containers instead of starting existing ones. (force-start)
- Enhance CLI safety with standardized error handling, input validation, and destructive operation prompts. (input-validation)
- Add structured logging and monitoring utilities for VPN containers and host metrics. (monitoring)
- - improve configuration reliability with atomic writes and automatic recovery
  - harden docker operations with retries, timeouts and orphan cleanup
  - replace regex diagnostics with targeted VPN health checks

  (stability-fixes)


## [0.3.1]

### Bug fixes

- Fixed creating and starting VPN services by automatically creating Docker containers from compose definitions. (#2)
- Show help when running the CLI without arguments and display a message for missing required arguments. (#123)


## [0.3.0]

### Features

- Add container diagnostics and health monitoring system with automated troubleshooting recommendations (#0)
- Add `--version`/`-V` option to display the application version. (#1)
- CLI restructure adds `system` command group, `--all` VPN flags, and deprecates `bulk` commands. (#2)


## [0.2.0]

### Bug fixes

- Fix module execution by providing a __main__ entry point. (#0)
- Replace deprecated license metadata and remove outdated classifier. (#9999)

### Features

- Add compose file validator command to validate Docker Compose configuration files (#1)
- Adds a global CLI option to specify a custom compose file path (via --compose-file/-f) and stores it in the Typer context for reuse across commands (#2)
- Add `init` command to generate an initial `compose.yml` and handle missing compose files more gracefully. (#123)


## [0.1.3]

### Bug fixes

- Fix various bugs including batch operations, prefix extraction, and gluetun configuration writes (1-6)
- Improve error handling for network requests with insecure SSL bypass option (#15)

### Documentation

- Update documentation including test suite README, CLAUDE.md, and README.md for Python implementation (docs-update)

### Features

- Create Python package structure for proxy2vpn CLI with proper setuptools configuration (#7)
- Refactor to use Docker Compose YAML files for configuration with ComposeManager and VPNService dataclasses (#8)
- Implement core Python functionality with config management and server list handling (#9)
- Add Python package installer for cross-platform installation (#14)
- Add bulk VPN container management operations and external IP address lookup (#16)
- Complete Python CLI implementation with individual container operations (start, stop, restart, logs, delete) (#17)
- Add VPN connection testing, location discovery commands, and preset utilities (#18)
- Improve CLI error messaging and help flags for better user experience (#19)

### Miscellaneous

- Add GitHub Actions workflow for automated Python testing with uv (#10)
- Add automated publish workflow for package distribution (#20)
- Integrate towncrier for automated changelog management (#21)

### Removals

- Remove legacy bash scripts and migrate to Python-only implementation (#22)


## [0.1.2] - 2025-05-20

### Added
- New 'diagnose' command to check problematic containers and analyze logs for errors
- Intelligent error detection and reporting for common VPN connection issues
- Special handling for containers stuck in restart loops

## [0.1.1] - 2025-05-20

### Fixed
- Fixed jq syntax issues in batch operations (using proper variable references)
- Fixed Docker device mount handling to properly pass device flags
- Improved profile loading in batch operations by directly parsing profile files

## [0.1.0] - 2025-05-20

### Added
- Initial stable release with proper versioning
- Added 'version' command to display script version
- Added CHANGELOG.md to track version history
- Docker Compose-like 'up' and 'down' commands for bulk operations
- 'cleanup' command to remove all VPN containers at once
- Support for multiple VPN providers
- User profiles for credential management
- Presets system for saving and reusing configurations
- Dynamic server list fetching from gluetun
- Container health monitoring
- HTTP proxy authentication
- Docker Compose import functionality
- Batch operations via JSON files
