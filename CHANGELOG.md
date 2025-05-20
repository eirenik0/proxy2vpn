# Changelog

All notable changes to Proxy2VPN will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
