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
- The VPNService class at src/proxy2vpn/models.py:25 handles container configuration
- Main CLI logic is in src/proxy2vpn/cli.py with Typer command groups
- You found Docker operations in src/proxy2vpn/docker_ops.py using docker-py SDK
- ComposeManager at src/proxy2vpn/compose_manager.py handles YAML file management
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
Hop 1: "Analyze CLI command structure in cli.py and identify command groups"
Hop 2: "Trace specific command implementation through models.py and compose_manager.py"
Hop 3: "Follow Docker operations in docker_ops.py and container lifecycle management"
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
Hop 2: "Analyze diagnostic system in diagnostics.py"
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
2. **Reference key modules** when known: cli.py, docker_ops.py, compose_manager.py, models.py
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
- Find diagnostic system entry points in cli.py
- Locate health checking logic in docker_ops.py
- Identify diagnostic data models in models.py

Step 2: Trace Monitoring Workflow
- How does 'vpn list --diagnose' command work?
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
- Diagnostic entry point is at cli.py:vpn_list() command with --diagnose flag
- Health checking logic found in docker_ops.py using Docker SDK
- DiagnosticResult class in diagnostics.py handles log analysis
- Container status checking uses docker.containers.get() API

Now analyze the specific diagnostic algorithms and scoring system:

Step 1: Analyze Log Analysis Patterns
- How does diagnostics.py parse container logs?
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
