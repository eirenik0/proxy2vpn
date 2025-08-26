# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Proxy2VPN is a Python command-line interface for managing multiple VPN containers with Docker. It supports user profiles, built-in health analysis in listings, fleet management for bulk deployments, and dynamic server list fetching using the qmcgaw/gluetun Docker image.

## Development Commands

### Building and Testing
```bash
# Install dependencies
uv sync
```

> [!NOTE]
> `uv` is part of the `uv` toolchain. If `uv` isn't installed, get it with:
> ```bash
> curl -LsSf https://astral.sh/uv/install.sh | sh
> ```

```bash
# Run the CLI
uv run proxy2vpn <command> [args]

# Run tests
make test

# Formatting and linting
make fmt
make lint

# Development installation
pip install -e ".[dev]"
```

### Running the Application
```bash
# Run using uv
uv run proxy2vpn <command> [args]

# Common commands
uv run proxy2vpn profile create myprofile            # interactive env file creator
# or add an existing env file
uv run proxy2vpn profile add myprofile profiles/myprofile.env
uv run proxy2vpn vpn create                         # interactive service creation
uv run proxy2vpn vpn start vpn1
uv run proxy2vpn vpn list
uv run proxy2vpn vpn start --all
uv run proxy2vpn servers list-providers
uv run proxy2vpn vpn test vpn1
uv run proxy2vpn system diagnose --verbose

# Fleet management commands
uv run proxy2vpn fleet plan --countries "Germany,France" --profiles "acc1:2,acc2:8"
uv run proxy2vpn fleet deploy --parallel
uv run proxy2vpn fleet status --show-allocation
uv run proxy2vpn fleet rotate --dry-run
```

### Profile Environment File Example
Profile environment files (e.g., profiles/myprofile.env) have comprehensive validation:
```bash
# Required fields (validated during profile creation)
VPN_TYPE=openvpn
VPN_SERVICE_PROVIDER=expressvpn
OPENVPN_USER=your_vpn_username
OPENVPN_PASSWORD=your_vpn_password

# Optional HTTP proxy (if enabled, credentials required)
HTTPPROXY=on
HTTPPROXY_USER=your_proxy_username
HTTPPROXY_PASSWORD=your_proxy_password
```

**Validation Rules**:
- `VPN_SERVICE_PROVIDER` - Required, must match supported gluetun provider
- `VPN_TYPE` - Optional, `openvpn` (default) or `wireguard`
- `OPENVPN_USER` - Required when `VPN_TYPE=openvpn`
- `OPENVPN_PASSWORD` - Required when `VPN_TYPE=openvpn`
- `HTTPPROXY_USER/PASSWORD` - Required only if `HTTPPROXY=on`

Profile creation fails fast with clear error messages if any required fields are missing.

## High-Level Architecture

### Application Organization
The Python application (`src/proxy2vpn/`) is organized into CLI, adapters, and core layers:
- `cli/main.py`: Typer application entry with command groups (`profile`, `vpn`, `servers`, `system`, `fleet`).
- `cli/commands/`: Command group implementations, one module per group.
- `adapters/compose_manager.py`: Docker Compose file management with ruamel.yaml and file locking.
- `adapters/docker_ops.py`: Docker container lifecycle operations via docker SDK.
- `adapters/server_manager.py`: Gluetun server list fetch/cache/validation.
- `adapters/display_utils.py`, `adapters/validators.py`, `adapters/logging_utils.py`: UI, input validation, logging helpers.
- `core/config.py`: Configuration constants, defaults, HTTP client settings, control API endpoints.
- `core/models.py`: Pydantic models and compose (de)serialization for `Profile` and `VPNService`.
- `core/services/diagnostics.py`: Diagnostic analyzer and health scoring used across commands.

### Docker Integration
All VPN containers use the `qmcgaw/gluetun` image with:
- Automatic network creation (proxy2vpn_network)
- Consistent labeling (e.g., `vpn.type=vpn`, provider/profile/location metadata)
- Environment variable injection for VPN configuration
- HTTP proxy authentication support

### Configuration System
- **compose.yml**: Single source of truth for services and profiles (profiles defined as YAML anchors)
- **profiles/*.env**: VPN settings (VPN_TYPE, OPENVPN_USER, OPENVPN_PASSWORD) referenced by profiles
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

## Version & Changelog Management

- Version is tracked in `pyproject.toml`.
- Add Towncrier fragments under `news/` (e.g., `123.feature.md`).
- Preview with `make changelog-draft`; build with `make changelog VERSION=x.y.z`.

## Key Implementation Details

### Error Handling
- Python exception handling with Typer error reporting
- Docker API error handling (NotFound, APIError) with user-friendly messages
- Graceful degradation for network failures and cache misses
- SSL certificate validation with bypass option for troubleshooting

### Key Dependencies
Core dependencies (see `pyproject.toml`):
- `typer`: CLI framework with command groups and rich help
- `docker`: Docker SDK for Python container operations
- `ruamel.yaml`: YAML processing with anchor/merge support for compose files
- `aiohttp`: Async HTTP client used for control API, IP checks, and server lists
- `pydantic`: Models and validation (profiles, services, HTTP config)
- `rich`: Console formatting for tables and progress
- `towncrier`: Changelog management (dev dependency)

### Server List Integration
ServerManager handles:
- Fetching from gluetun's official servers.json on GitHub
- Local caching in ~/.cache/proxy2vpn/ with 24-hour TTL
- Location validation for providers, countries, and cities
- CLI commands: list-providers, list-countries, list-cities, validate-location


### Fleet Management Architecture
The fleet system enables enterprise-scale VPN deployments:
- **FleetManager**: Orchestrates bulk deployments across cities with intelligent resource allocation
- **ProfileAllocator**: Manages VPN account slots with round-robin load balancing (e.g., acc1:2, acc2:8)
- **ServerMonitor**: Provides health monitoring and automatic server rotation for failed services
- **DeploymentPlan**: YAML-serializable configuration for reproducible fleet deployments
- **Rich UI Integration**: Interactive tables, progress indicators, and real-time status updates

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

### Code Quality
- Run `make fmt` and `make lint` after code changes
- After implementing features, update the `/news` folder with appropriate fragments
- Ensure Docker containers are properly tested before committing changes

## INSTRUCTIONS: Using Codanna-Navigator Agent Effectively

### Multi-Hop Pattern for Deep Analysis

When analyzing complex features or implementation requirements in the proxy2vpn codebase, you (Claude) MUST use the codanna-navigator agent in a multi-hop fashion for comprehensive understanding. Single-shot prompts often miss critical details that only emerge through iterative exploration.

### Key Principles

1. **Start Broad, Then Deep**: First prompt should map the landscape, follow-ups should drill into specifics
2. **Track Progress**: Always instruct the agent to use TodoWrite for complex analyses
3. **Score and Iterate**: Mentally score each response (0-10) and identify gaps to fill
4. **Context Preservation**: Each follow-up should reference findings from previous hops
5. **Structured Prompts**: Break down complex requests into numbered steps with clear deliverables

### CRITICAL: Context Forward-Passing

Since the codanna-navigator agent starts fresh with each invocation, you MUST pass forward relevant context from previous hops. This dramatically improves the agent's precision and efficiency.

**What to Forward**:
- Specific file paths and line numbers discovered
- Class/function names that need investigation
- Patterns or conventions identified
- Key findings that narrow the search scope
- Relationships between components

**Example Context Forwarding**:
```
"CONTEXT FROM PREVIOUS ANALYSIS:
- The VPNService class at src/proxy2vpn/core/models.py handles container configuration
- Main CLI entry is at src/proxy2vpn/cli/main.py with Typer command groups
- Docker operations live in src/proxy2vpn/adapters/docker_ops.py (docker SDK)
- ComposeManager at src/proxy2vpn/adapters/compose_manager.py handles YAML management
- Profile management uses environment file references in profiles/*.env

Now focus on: [specific targeted request based on above context]"
```

This context forwarding transforms vague searches into surgical strikes, allowing the agent to navigate directly to relevant code instead of searching blindly.

### When to Use Multi-Hop

- **Feature Implementation Planning**: Understanding impact across CLI commands, Docker operations, and configuration management
- **Refactoring Analysis**: Identifying all touchpoints between CLI, Docker operations, and compose file management
- **Architecture Understanding**: Mapping relationships between Typer CLI, Docker SDK, and YAML configuration
- **Pattern Discovery**: Finding consistent patterns across command groups and container management

### Proxy2VPN-Specific Analysis Patterns

#### Pattern 1: CLI Command → Implementation → Docker Operations
```
Hop 1: "Analyze CLI command structure in cli/main.py and identify command groups"
Hop 2: "Trace a command through core/models.py and adapters/compose_manager.py"
Hop 3: "Follow Docker operations in adapters/docker_ops.py and container lifecycle management"
```

#### Pattern 2: Configuration Flow Analysis
```
Hop 1: "Map configuration flow from CLI args to compose files"
Hop 2: "Analyze profile management and environment file handling"
Hop 3: "Trace container configuration to Docker API calls"
```

#### Pattern 3: Error Handling and Diagnostics
```
Hop 1: "Find error handling patterns across CLI and Docker operations"
Hop 2: "Analyze diagnostic system in core/services/diagnostics.py"
Hop 3: "Trace troubleshooting workflow and log analysis"
```

### Common Multi-Hop Patterns

#### Architecture → Details → Examples
```
Hop 1: "Map the architecture of [VPN management/CLI structure/Docker integration]"
Hop 2: "Drill into specific implementations of [container lifecycle/profile management/server list caching]"
Hop 3: "Show concrete examples of [Docker API usage/YAML anchor patterns/Typer command structure]"
```

#### Current State → Impact → Migration
```
Hop 1: "Analyze current implementation of [container management/CLI commands/configuration system]"
Hop 2: "Identify all dependencies and touchpoints across modules"
Hop 3: "Propose enhancement strategy with examples"
```

#### Search → Filter → Deep Dive
```
Hop 1: "Find all occurrences of [Docker API calls/Typer decorators/YAML operations]"
Hop 2: "Filter to [specific command groups/container operations/error cases]"
Hop 3: "Analyze top candidates with line numbers and implementation details"
```

### Agent Prompt Best Practices for Proxy2VPN

1. **Always include TodoWrite instruction** for complex tasks
2. **Reference key modules** when known: cli/main.py, adapters/docker_ops.py, adapters/compose_manager.py, core/models.py
3. **Specify output format**: Ask for line numbers, class/method names, Docker API patterns
4. **Focus on integration points**: How CLI commands trigger Docker operations via models
5. **Request examples** of Typer command patterns, Docker SDK usage, YAML anchor usage
6. **Ask for error handling patterns** specific to Docker API and network operations

### Example Multi-Hop Analysis for Proxy2VPN

**Use Case**: Understanding how VPN container health monitoring works

**First Hop - Architecture Overview**:
```
"Analyze the proxy2vpn codebase to understand the health monitoring and diagnostic system.

Step 1: Map Health Monitoring Components
- Find diagnostic system entry points in CLI command modules
- Locate health checking logic in adapters/docker_ops.py
- Identify diagnostic analyzer in core/services/diagnostics.py

Step 2: Trace Monitoring Workflow
- How does 'vpn list' compute health by default?
- What Docker API calls are used for health checks?
- How are container logs analyzed?

Step 3: Identify Key Integration Points
- Connection between CLI commands and Docker operations
- How diagnostic results are formatted and displayed
- Error handling for container communication failures

Provide line numbers and specific class/method names. Use TodoWrite to track progress."
```

**Second Hop - Deep Dive with Context**:
```
"CONTEXT FROM PREVIOUS ANALYSIS:
- Diagnostic entry point is in cli/commands/vpn.py (list command)
- Health checking logic found in adapters/docker_ops.py using Docker SDK
- Diagnostic analyzer in core/services/diagnostics.py handles log analysis and scoring
- Container status checking uses docker.containers.get() API

Now analyze the specific diagnostic algorithms and scoring system:

Step 1: Analyze Log Analysis Patterns
- How does core/services/diagnostics.py parse container logs?
- What specific error patterns does it look for?
- How is the health score calculated?

Step 2: Docker API Integration Details
- Specific Docker SDK methods used for container inspection
- How container network status is determined
- Error handling for Docker API failures

Step 3: Provide Enhancement Examples
- Show current diagnostic output format
- Identify gaps in current health checking
- Suggest specific improvements with code examples"
```

### Red Flags Requiring Follow-Up in Proxy2VPN Context

- Agent mentions "Docker operations" without specific API calls → Ask for exact docker-py SDK usage
- Agent describes CLI structure without Typer decorator patterns → Request specific command group examples
- Agent analyzes configuration without YAML anchor details → Ask for compose file structure specifics
- Agent discusses error handling without Docker exception types → Request specific exception handling patterns

### Remember for Proxy2VPN

The codanna-navigator agent excels at understanding:
- Python module structure and imports
- Typer CLI framework patterns  
- Docker SDK API usage
- YAML file structure and anchors
- Class inheritance and method relationships
- Error handling patterns across async/sync operations

Use these strengths by crafting prompts that leverage the agent's understanding of Python package structure and the specific frameworks used in proxy2vpn.
