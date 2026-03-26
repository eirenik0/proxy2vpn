# Changelog

All notable changes to Proxy2VPN will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

<!-- towncrier release notes start -->
## [0.17.0]

### Bug fixes

- Run agent health diagnostics in a worker thread so incident checks stop tripping the sync-only `fetch_ip()` helper. (#220)
- Fixed fleet/server rotation to update VPN service locations through the mutable config model instead of assigning to the read-only `VPNService.location` property. (#222)
- The watchdog now treats isolated auth failures on an otherwise healthy shared profile as service-specific and restarts the tunnel before opening an auth incident, while investigations degrade gracefully on peer probe failures and only use matching auth/config peer evidence for profile-wide conclusions. (#224)
- Rotation now retries verification on the same candidate with `vpn test` semantics before switching cities, logs per-service rotation attempts and fails after three city attempts per server, tracks recently bad cities to avoid during the same run, defers daemon-triggered rotation escalation until a service stays degraded for more than five minutes, renames the compose service/container to match the new city, migrates other active incidents to the renamed service so watchdog resolution still works, records requested and final rotation changes for later agent investigation, only succeeds when the replacement endpoint becomes healthy with a verifiably new egress IP, and avoids showing a health score of 0 when connectivity is confirmed but stale logs still contain older failures. (#225)
- Block `fleet deploy` while the local agent watchdog is running to avoid deployment-state races. (#226)
- Isolate batch health assessment failures, improve country-scoped breaker parsing for multi-word countries, and clean up failed VPN starts so created containers do not linger on port conflicts. (#227)
- Skip rollback cleanup for forced fleet deploy failures, since there is no prior working state to restore. (#228)
- Fix fleet deploy summaries so forced parallel deploys report partial success and current fleet status correctly. (#229)
- Proxy2VPN now emits the control auth file as a bind mount and validates compose files that accidentally treat it as a named volume. (#230)

### Features

- Add a local `proxy2vpn agent` watchdog with compose-root state, safe automatic remediation, and approval-based service rotation. (#215)
- Add optional OpenAI-backed incident enrichment for the local agent using `gpt-5-nano` and structured responses. (#216)
- Add detached `proxy2vpn agent run --daemon` and `proxy2vpn agent stop` commands with PID and log-file tracking. (#218)
- Added `proxy2vpn agent investigate` to persist incident investigations and print operator action plans for watchdog incidents. (#221)
- `system diagnose` now reports the Docker network interconnection state for `proxy2vpn_network` alongside per-container health. (#231)

### Miscellaneous

- Changed the GitHub release workflow to publish immediately instead of creating a draft release first. (#214)
- Move watchdog settings into a dedicated `proxy2vpn.agent.config` module and centralize agent env overrides there. (#217)
- Moved Gluetun control auth environment handling into a typed `pydantic-settings` config instead of parsing env vars directly in the HTTP client. (#223)


## [0.16.1]

### Bug fixes

- Made Docker log reads retry briefly after container startup so early output is not missed in CI. (#213)


## [0.16.0]

### Bug fixes

- Fixed fleet and profile command handling so provider resolution, compose-file context, control-port planning, and fleet status output behave correctly. (#2)
- Fix generated compose services to use real YAML merges and allow profile-only workspaces to pass validation. (#201)
- Resolve profile env files and control auth config relative to the compose root instead of the shell working directory. (#202)
- Hardened compose merge serialization during bulk fleet deployment so adding many services does not fail on ruamel YAML merge internals. (#206)
- Pinned `ruamel.yaml` for reproducible CI and made Docker tests provision their own control auth config instead of relying on local workspace state. (#207)
- Fleet planning now appends safely by reusing the next free ports and auto-suffixing duplicate service names instead of colliding with existing services. (#209)

### Documentation

- Updated the README and contributor docs for `vpn add`, explicit `vpn update`, compose-root state files, and the supported test commands. (#205)
- Documented the additive fleet expansion workflow, including the next deploy step and how duplicate city names are auto-suffixed. (#210)

### Features

- Implement reliable `fleet scale` and `fleet rotate` commands with profile support

  - **Fleet rotation**: Parallel health checking, atomic operations with rollback capability, and smart server selection
  - **Fleet scaling**: Support for scaling up/down with specific profile selection, atomic port allocation, and proper cleanup
  - **Reliability improvements**: Singleton FleetStateManager with async locks, batch operations, and comprehensive error handling
  - **Profile integration**: Scale operations can target specific profiles or use default profile allocation

  (#1)
- Add `vpn add` as the compose-only command for defining VPN services explicitly or interactively. (#203)
- Added `proxy2vpn vpn update` as the explicit command to pull, recreate, and restart VPN containers. (#204)
- Added a GitHub-native release workflow that prepares draft GitHub Releases and publishes to PyPI when a release is published. (#212)

### Miscellaneous

- Updated GitHub Actions to use Makefile targets and Node 24-compatible `checkout`/`setup-uv` action versions. (#208)
- Switched the PyPI release workflow to Node 24-compatible GitHub Actions and Trusted Publishing. (#211)

### Removals

- Remove `profile apply` and the old `vpn create` service-definition command in favor of `vpn add`. (#203)
- `proxy2vpn vpn start` and `proxy2vpn vpn restart` no longer hide image refresh and container recreation; use `proxy2vpn vpn update` for that workflow. (#204)


## [0.15.0]

### Bug fixes

- vpn export-proxies now exports only services defined in the project compose file (no extra containers) and adds a new `provider` column to the CSV output. (#156)
- incorrect `tunnel-restart` on older gluetun versions (#157)
- Fix misleading "Missing required arguments" message

  - The CLI no longer misreports unrelated runtime errors as "Missing required arguments".
  - This prevents masking real errors (e.g., network or auth issues) when running commands like `vpn public-ip` with a SERVICE argument.

  (#159)
- Compose generation robustness improvements

  - Place profile definitions before services in generated compose files to ensure valid anchors and references.
  - Avoid duplicate profile anchors when generating profiles.
  - Allow `fleet deploy -f` to overwrite compose services as expected.

  (#166)
- Control API schema compatibility

  - Accept `public_ip` as an alternative to `ip` and normalize internally for `vpn public-ip`.
  - Accept `outcome` as an alternative to `status` for OpenVPN status and normalize internally.

  (#167)

### Features

- Add multiprovider support for `fleet plan` based on profile vpn provider (#1)
- Validate profile env fields (#2)
- Use Pydantic models for configuration validation. (#4)
- Replace `create` with `add` command. Add interactive `create` command. (#6)
- validate VPN provider during interactive profile creation (#7)
- Validate VPN_PROVIDER against available server list during profile environment validation. (#8)
- Integrated Gluetun control server checks into diagnostics. Added CLI commands for DNS status, updater status, and port forwarding. (#10)
- Mount control server auth config in compose (#152)
- Rename `profile delete` to `profile remove` and introduce `profile delete` to remove profile environment files. (#153)
- Create 'control-server-auth.toml' only during `system init`. (#154)
- Add support for optional `VPN_TYPE` in profile environment files with validation for `openvpn` or `wireguard` (default `openvpn`). (#155)
- Interactive VPN service creation replaces argument-based `vpn create` command. 
  `proxy2vpn vpn create` now prompts for service name, profile and ports interactively. (#162)
- Add interactive profile selection when creating VPN services with `vpn create`. (#163)
- Make diagnostic health analysis default in VPN list command by removing --diagnose and --ips-only options (#164)

### Miscellaneous

- refactor: Centralize compose env/port parsing and harden Pydantic models at API boundaries to reduce special cases and avoid breaking on upstream field drift. (#5)
- Change VPN_PROVIDER to VPN_SERVICE_PROVIDER in profile (#11)
- Speedup health check (#165)

### Removals

- Drop the deprecated --provider flag from `fleet plan`; providers now come solely from profile env files. (#3)
- Remove `--provider` option from `vpn create`; provider is now inferred from the profile. (#9)


## [0.13.0]

### Bug fixes

- Fix Docker network cleanup by force-removing networks when initial deletion fails. (#138)
- Fix `fleet plan` to validate profile names and prevent deployment failures from missing profiles. (#154)
- Fix proxy accessibility by binding proxy ports to 0.0.0.0 while keeping control ports localhost-only. (#155)
- Fix server list loading errors. (#156)
- Fix fleet deployments to include SERVER_COUNTRIES environment variable alongside city information. (#220)
- Fix Gluetun control API 401 errors by mounting authentication config that disables credentials for control routes. (#242)

### Features

- Replace `--recreate-network` flag with `--force` in `fleet deploy` command to rebuild containers and network. (#121)
- Add provider column to `vpn list` command output. (#143)
- Add location validation for VPN commands with support for city, country, or city,country formats and `--force` bypass option. (#200)
- Add `start_vpn_service` helper function to consolidate VPN container startup logic across fleet operations. (#240)
- Add service status counting helper for improved fleet status reporting. (#300)

### Miscellaneous

- Adopt built-in collection generics and PEP 604 union types. (#150)
- Refactor codebase for improved maintainability. (#151)
- Reorganize code into separate modules for better structure. (#152)


## [0.12.0]

### Bug fixes

- Fixed missing --recreate-network option in fleet deploy command help output (#80)
- Fixed critical type safety issues in CLI commands and Docker operations that were causing 33+ type checker errors, improving code reliability and maintainability. (#81)

### Features

- Add comprehensive VPN control API system including aiohttp-based control client helpers, GluetunControlClient subclass with typed responses, HTTP control server on port 8000, CLI commands for querying status/public IP/tunnel restart, and control server port allocation and validation. (#68)
- Include direct and proxied IP addresses in connectivity diagnostics and system diagnose output. (#79)

### Miscellaneous

- Added asynchronous HTTP client with configurable retries and request timing metrics. Refactored IP address lookup, server list downloads, and proxy health checks to use centralized HTTPClient with sync and async support. (#72)


## [0.11.0]

### Bug fixes

- Fix fleet health checks by using authenticated proxy URLs during validation. (#64)
- fix empty profile allocation table output in `fleet status` (#123)

### Features

- feat: add --unique-ips option to fleet plan to ensure unique city/IP routes (#124)


## [0.10.0]

### Bug fixes

- Fix fleet deployments failing due to passing Docker container objects instead of service names to `start_container`. (#8)

### Documentation

- Add note on installing uv and streamline installation instructions to use uvx. (#5)

### Features

- Add numbered index column (N) to all list commands for easy reference (#1)
- Add `--verbose` logging option to system diagnose command for detailed troubleshooting (#2)
- Add optional file logging; logs are disabled when no file is specified. (#6)
- Add option to recreate Docker network before fleet deployments to avoid network name conflicts. (#7)

### Miscellaneous

- Add proper console output. (#3)
- Add test run parallelization (#4)


## [0.9.0]

### Bug fixes

- Add missing fleet commands module (#124)

### Features

- Add `vpn export-proxies` command to export running VPN proxies to a CSV file. (#123)


## [0.8.0]

### Bug fixes

- - handle non-IP responses from external services when retrieving public IPs (fetch-ip-html)

### Features

- Add location column to `proxy2vpn vpn list` output. (location-column)

### Removals

- Remove deprecated `bulk` command group. (remove-bulk)


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
