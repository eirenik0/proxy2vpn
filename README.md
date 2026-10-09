# Proxy2VPN

[![PyPI version](https://badge.fury.io/py/proxy2vpn.svg)](https://badge.fury.io/py/proxy2vpn)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/release/python-3100/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Enterprise-grade VPN container orchestration for developers who need reliable proxy infrastructure.**

Stop wrestling with VPN clients that crash, managing multiple accounts manually, or dealing with inconsistent proxy setups. Proxy2VPN turns Docker containers into a fleet of rock-solid VPN endpoints you can deploy, monitor, and scale across dozens of countries in minutes.

## Why Proxy2VPN?

**The Problem**: You need reliable proxy infrastructure for testing, scraping, or accessing geo-restricted content. Traditional VPN clients are unreliable, managing multiple accounts is painful, and scaling across regions is a nightmare.

**The Solution**: Containerized VPN services that just work. Deploy 50 VPN endpoints across 20 countries with a single command. Load-balance across multiple accounts automatically. Monitor health and rotate failed servers without intervention.

**Real-world use cases**:
- Web scraping with rotating IP addresses across multiple countries
- Testing geo-restricted applications from different regions  
- Load balancing traffic across multiple VPN accounts
- Creating development environments that mirror production geography
- Building resilient proxy infrastructure for CI/CD pipelines

## Key Features

- **Fleet Management**: Deploy VPN containers across multiple countries and cities in parallel
- **Profile-based Credentials**: Manage multiple VPN accounts as reusable configurations
- **Intelligent Load Balancing**: Distribute connections across accounts automatically
- **Health Monitoring**: Auto-rotate failed servers and maintain uptime
- **HTTP Proxy Support**: Built-in authenticated proxy endpoints for each VPN
- **Provider Agnostic**: Works with ProtonVPN, NordVPN, ExpressVPN, and 30+ providers via gluetun

## Requirements

- Docker and Docker Compose
- Python 3.10+
- `curl` on the host running `proxy2vpn`
- A VPN account from any [supported provider](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers)

`proxy2vpn` uses `curl` under the hood for proxied public-IP and health checks against authenticated Gluetun HTTP proxies. This path proved more reliable than Python HTTP clients for those proxy checks, and CI verifies that `curl` is available.

## Quick Installation

```bash
# Install via uvx (recommended - no global dependencies)
uvx proxy2vpn --help

# Or install globally
pip install proxy2vpn
```

> **Note**: `uvx` is part of the [uv](https://github.com/astral-sh/uv) toolchain. Install with: `curl -LsSf https://astral.sh/uv/install.sh | sh`

## 5-Minute Quick Start

Get a VPN endpoint running in under 5 minutes:

```bash
# 1. Initialize your workspace
proxy2vpn system init

# 2. Create your first profile with VPN credentials (all fields required)
mkdir -p profiles
cat <<'EOF' > profiles/production.env
VPN_TYPE=openvpn
VPN_SERVICE_PROVIDER=protonvpn
OPENVPN_USER=your_protonvpn_username
OPENVPN_PASSWORD=your_protonvpn_password
HTTPPROXY=on
HTTPPROXY_USER=proxy_user
HTTPPROXY_PASSWORD=proxy_pass
EOF

# 3. Register the profile and define a VPN service
# Option A: Add the env file you just created
proxy2vpn profile add production profiles/production.env

# Option B: Create the env file interactively (no manual file needed)
# proxy2vpn profile create production

# Add a VPN service interactively (choose name/profile/ports when prompted)
proxy2vpn vpn add --interactive

# 4. Start and test your VPN
proxy2vpn vpn start london-proxy
proxy2vpn vpn test london-proxy

# 5. Use your proxy (HTTP proxy now available on localhost:8888)
curl --proxy http://proxy_user:proxy_pass@localhost:8888 https://httpbin.org/ip
```

**That's it!** Your VPN container is running and you have an authenticated HTTP proxy endpoint.

## Quickstarts by Use Case (USA)

These short flows cover common US-focused setups. Location names must be valid for your provider (use `proxy2vpn servers list-cities <provider> "United States"` to explore).

### Single US Proxy (Local Dev)

```bash
# 1) Create a profile
proxy2vpn profile create us-dev

# 2) Define a service interactively (choose profile, ports, and location)
proxy2vpn vpn add --interactive
# Suggested answers:
# - Service name: us-nyc
# - Profile: us-dev
# - Host port: 8888 (or 0 for auto)
# - Control port: 0 (auto)
# - Location: "New York, United States"

# 3) Start and test
proxy2vpn vpn start us-nyc
proxy2vpn vpn public-ip us-nyc
curl --proxy http://user:pass@localhost:8888 https://httpbin.org/ip
```

### East/West Geo Testing (US)

```bash
# Create two services interactively
proxy2vpn vpn add --interactive
# - Service name: us-east
# - Profile: us-dev
# - Host port: 20001 (or auto)
# - Control port: 0 (auto)
# - Location: "New York, United States"

proxy2vpn vpn add --interactive
# - Service name: us-west
# - Profile: us-dev
# - Host port: 20002 (or auto)
# - Control port: 0 (auto)
# - Location: "Los Angeles, United States"

# Start and compare
proxy2vpn vpn start --all
proxy2vpn vpn list
```

### US Scraping Fleet (Multiple Endpoints)

```bash
# Plan a small fleet in the US (10 endpoints using a single profile)
proxy2vpn fleet plan --countries "United States" --profiles "us-dev:10" --unique-ips

# Deploy in parallel and check status
proxy2vpn fleet deploy --parallel
proxy2vpn fleet status --show-allocation
```

### Expanding an Existing Fleet

If you add another VPN account/profile later, treat it as an additive deployment:

```bash
# Add a new profile first
proxy2vpn profile add expressvpn-extra profiles/expressvpn-extra.env

# Plan only the extra capacity you want to add
proxy2vpn fleet plan \
  --countries "Germany,France" \
  --profiles "expressvpn-extra:4" \
  --output add-expressvpn-extra.yaml

# Next step: deploy that plan
proxy2vpn fleet deploy --plan-file add-expressvpn-extra.yaml --parallel --validate-first

# Confirm allocation after the new services are added
proxy2vpn fleet status --show-allocation
```

Notes:

- `fleet deploy` is additive by default. It appends new services to the existing compose file.
- Use `--force` only when you explicitly want to recreate/replace existing fleet services.
- When a planned city name already exists, proxy2vpn automatically keeps the old service and creates a new name such as `expressvpn-germany-frankfurt-2`.
- Ports are planned from the next free values, so adding a second plan does not reuse ports that are already occupied.
- If you want names that reflect the profile used, set `--naming-template "{provider}-{profile}-{country}-{city}"`.

### CI: Ephemeral US Proxies

```bash
# Generate a reproducible plan file for CI
proxy2vpn fleet plan --countries "United States" --profiles "ci:3" --output ci-us-fleet.yml

# Validate or dry-run during pipeline
proxy2vpn fleet deploy --plan-file ci-us-fleet.yml --dry-run

# Deploy only when needed, then tear down
proxy2vpn fleet deploy --plan-file ci-us-fleet.yml --parallel --validate-first
proxy2vpn fleet scale down --factor 0
```

## Container Management & Monitoring

Each VPN container exposes both HTTP proxy endpoints and control APIs for programmatic management:

```bash
# Check VPN status and public IP
proxy2vpn vpn status london-proxy
proxy2vpn vpn public-ip london-proxy

# Monitor logs and restart tunnels
proxy2vpn vpn logs london-proxy --follow
proxy2vpn vpn restart-tunnel london-proxy

# Bulk operations across all services
proxy2vpn vpn start --all
proxy2vpn vpn update --all
proxy2vpn vpn list
```

**Docker Integration**: All containers use consistent labeling and networking, making them easy to integrate with existing Docker workflows and monitoring tools.

**Health-check implementation note**: when `proxy2vpn` validates a VPN proxy endpoint, the proxied public-IP check uses `curl` under the hood. Direct control API calls and non-proxied HTTP requests still use the Python HTTP client stack.

### Compose-root state

`proxy2vpn` keeps generated support files next to the active compose file.
This makes the workspace portable and avoids cwd-dependent behavior.

- `proxy2vpn system init --compose-file state/compose.yml` creates `state/control-server-auth.toml`
- `proxy2vpn profile add NAME relative/path.env` stores the relative path in `compose.yml`
- runtime commands resolve profile env files and the control auth file relative to the compose file, not the shell's current working directory

### Control server authentication

`proxy2vpn system init` generates `control-server-auth.toml` and
`control-client-auth.json` next to the active Compose file. Both use owner-only
permissions. Separate random credentials authorize monitoring reads and recovery
writes; the CLI and watchdog discover them automatically from the Compose root.
Keep both files together when moving a workspace. Neither role exposes settings
that may contain credentials.

Existing custom and legacy authentication files are preserved, including when
initializing with `--force`. For custom auth, continue to configure
`GLUETUN_CONTROL_AUTH=user:password`. Do not edit files carrying the generated
header independently: clients verify that the server/client pair matches.

New VPN proxies bind to `127.0.0.1` by default. Select an explicit private or public
interface with `vpn add --proxy-bind-address ADDRESS`; fleet configuration files
and saved deployment plans accept `proxy_bind_address` too. Existing Compose
bindings are preserved when loading, updating, restoring, or rotating services.
Remote clients need a reachable selected interface; an authenticated proxy still
needs a protected client-to-proxy network connection.

To explicitly migrate an existing workspace:

```bash
proxy2vpn --compose-file state/compose.yml system secure --replace-control-auth
proxy2vpn --compose-file state/compose.yml vpn update --all
```

The first command backs up authentication files and Compose, prepares generated
authentication, and changes proxy bindings to localhost. It does not modify live
containers. The second recreates VPN containers to apply the files and interrupts
existing connections. Choose `--proxy-bind-address YOUR_PRIVATE_HOST_IP` on the
first command if remote private clients need access, or explicitly choose
`0.0.0.0` to publish on all IPv4 interfaces.

Without `--replace-control-auth`, the command preserves existing authentication.
Repeating migration preserves valid generated credentials. To roll back, restore
`compose.yml.bak`, `control-server-auth.toml.bak`, and (if one existed)
`control-client-auth.json.bak` together, then recreate the services. When rolling
back to legacy/custom auth that had no client file, remove the newly generated
client file. Backups contain credentials and must stay private.

## External HTTP CONNECT proxies

External endpoints can join health checks and watchdog incidents without a local
Docker container. Save `external-proxies.json` next to the active Compose path:

```json
{
  "version": 1,
  "endpoints": [{
    "id": "office-proxy",
    "connection": {
      "protocol": "http_connect",
      "host": "proxy.example.com",
      "port": 3128
    },
    "credentials": {
      "username_env": "OFFICE_PROXY_USER",
      "password_env": "OFFICE_PROXY_PASSWORD"
    },
    "expected_egress_ips": ["203.0.113.10"]
  }]
}
```

Set those credential variables in the watchdog environment. Omit `credentials`
for anonymous proxies; only variable names belong in the JSON file. Endpoint ids
must be unique and must not collide with Compose service names. Existing Compose
files need no changes. An external-only workspace can omit the Compose file:
its selected path still determines the configuration and `.proxy2vpn-agent/` root.

```bash
proxy2vpn -f /path/to/workspace/compose.yml agent run --once
proxy2vpn -f /path/to/workspace/compose.yml agent status --json --live
proxy2vpn -f /path/to/workspace/compose.yml agent incidents --json
proxy2vpn -f /path/to/workspace/compose.yml fleet status --show-health --format json --no-show-allocation
```

`PROXY2VPN_AGENT_EXTERNAL_PROXIES_FILE` overrides the JSON path for watchdog and
fleet status; relative paths resolve next to the selected Compose path. Optional
`probe_urls` overrides the default HTTPS IP reflectors (`ipinfo.io/ip` and
`ifconfig.me/ip`). Each probe must return a complete IP literal. TLS certificates
are verified, redirects are disabled, and failed requests never fall back to a
direct host connection. The proxy hostname resolves locally; CONNECT sends the
target hostname for proxy-side DNS. The proxy hop itself uses HTTP; the target
connection uses TLS. Ambient proxy variables and netrc are ignored.

Health requires request connectivity and observed IP evidence. Missing evidence
is unknown; HTTP 407 during CONNECT means rejected proxy authentication. The optional
`expected_egress_ips` allowlist checks egress identity. Without it, a successful
probe confirms the configured proxy route, without claiming geography, anonymity,
or vendor identity. Sharing the host's public IP is allowed. Credentials,
response bodies, and raw request exceptions are excluded from diagnostics.

The first adapter offers health and investigation only. It cannot restart,
restore, replace endpoints/sessions, or request an exit-IP change. Unhealthy
endpoints create operator-investigation incidents; healthy observations resolve
them using the same configured id. Each assessment opens a new client session,
with no sticky-session or stable-exit-IP guarantee. Compose deployment, allocation,
and rotation commands continue to manage Gluetun services.

## Enterprise Fleet Management

**The real power of Proxy2VPN**: Deploy and manage dozens of VPN endpoints across the globe like infrastructure, not individual connections.

### Multi-Provider Fleet Orchestration (New!)

**Automatic provider orchestration**: Mix ExpressVPN, NordVPN, ProtonVPN in a single deployment. Each profile specifies its provider - the system coordinates everything automatically.

```bash
# Create profiles with provider information
cat <<'EOF' > profiles/expressvpn-main.env
VPN_TYPE=openvpn
VPN_SERVICE_PROVIDER=expressvpn
OPENVPN_USER=your_expressvpn_username
OPENVPN_PASSWORD=your_expressvpn_password
HTTPPROXY=on
HTTPPROXY_USER=proxy_user
HTTPPROXY_PASSWORD=proxy_pass
EOF

cat <<'EOF' > profiles/nordvpn-backup.env
VPN_TYPE=openvpn
VPN_SERVICE_PROVIDER=nordvpn
OPENVPN_USER=your_nordvpn_username
OPENVPN_PASSWORD=your_nordvpn_password
HTTPPROXY=on
HTTPPROXY_USER=proxy_user
HTTPPROXY_PASSWORD=proxy_pass
EOF

# Register profiles
proxy2vpn profile add expressvpn-main profiles/expressvpn-main.env
proxy2vpn profile add nordvpn-backup profiles/nordvpn-backup.env
proxy2vpn profile add protonvpn-fleet profiles/protonvpn-fleet.env

# Single command deploys across ALL providers automatically
proxy2vpn fleet plan \
  --countries "Germany,France,Netherlands,United Kingdom,United States" \
  --profiles "expressvpn-main:6,nordvpn-backup:4,protonvpn-fleet:8"

# Deploy multi-provider fleet in one operation
proxy2vpn fleet deploy --parallel
```

**Result**: 18 endpoints automatically distributed across 3 VPN providers, with coordinated port allocation and intelligent load balancing.

If you later add more capacity with another account from the same provider, run a new `fleet plan` for just the additional slots and deploy that plan file. Reusing the same countries or cities is supported: names are auto-suffixed on collisions, and `--naming-template "{provider}-{profile}-{country}-{city}"` makes profile ownership explicit.

### Scenario: Global Web Scraping Infrastructure

You need maximum IP diversity for scraping across 15 countries:

```bash
# Plan deployment: 20 endpoints across multiple providers for maximum diversity
proxy2vpn fleet plan \
  --countries "Germany,France,Netherlands,United Kingdom,United States,Canada" \
  --profiles "expressvpn-main:8,nordvpn-backup:6,protonvpn-fleet:6" \
  --unique-ips

# Deploy everything in parallel (typically completes in 2-3 minutes)
proxy2vpn fleet deploy --parallel --validate-first

# Check your fleet status - shows provider distribution
proxy2vpn fleet status --show-allocation
```

**Result**: 20 HTTP proxy endpoints across 3 different VPN providers, each with unique IP addresses for maximum scraping diversity.

### Scenario: CI/CD Pipeline Testing

Your application needs testing from different geographic regions:

```bash
# Create a test fleet for your CI pipeline
proxy2vpn fleet plan \
  --countries "Germany,Singapore,United States" \
  --profiles "ci-testing:3" \
  --output ci-fleet.yaml

# Deploy only when tests run
proxy2vpn fleet deploy --plan-file ci-fleet.yaml --dry-run
```

### Automatic Health Management

Fleet management includes intelligent health monitoring:

```bash
# Monitor and rotate failed endpoints automatically
proxy2vpn fleet rotate --criteria performance

# Scale up during high-demand periods
proxy2vpn fleet scale up --countries "United States,Germany" --factor 2

# Scale down to save resources
proxy2vpn fleet scale down --factor 0.5
```

Fleet management handles the complexity so you focus on your application, not infrastructure.

## Common Use Cases

### Web Scraping at Scale
```bash
# Multiple IPs across regions to avoid rate limiting
proxy2vpn fleet plan --countries "US,UK,DE,FR,CA" --profiles "scraping:10"
proxy2vpn fleet deploy --parallel

# Use any endpoint: curl --proxy http://user:pass@localhost:20001 https://api.example.com
```

### Geo-location Testing
```bash
# Test your app from different countries
proxy2vpn vpn add --interactive
# - Service name: us-east
# - Profile: production
# - Host port: 0
# - Control port: 0
# - Location: "New York"

proxy2vpn vpn add --interactive
# - Service name: eu-west
# - Profile: production
# - Host port: 0
# - Control port: 0
# - Location: "Amsterdam"
proxy2vpn vpn start --all
```

### CI/CD Pipeline Integration
```bash
# Include in your test pipeline
proxy2vpn fleet plan --countries "Germany,Singapore" --profiles "ci:2" --output tests/fleet.yaml
proxy2vpn fleet deploy --plan-file tests/fleet.yaml --validate-first
# Run your geo-specific tests
proxy2vpn fleet scale down --factor 0  # Clean up after tests
```

### Development Environment
```bash
# Persistent development proxies
proxy2vpn vpn add --interactive
# - Service name: dev-proxy
# - Profile: dev-account
# - Host port: 8888
# - Control port: 0
# - Location: "Netherlands"
# Always available at localhost:8888 for your development
```

## Essential Commands

### System operations
- `proxy2vpn system init [--force]`
- `proxy2vpn system validate`
- `proxy2vpn system diagnose [--lines N] [--all] [--verbose] [--json]`

### Profiles
- `proxy2vpn profile create NAME` (interactive env file creator)
- `proxy2vpn profile add NAME ENV_FILE`
- `proxy2vpn profile list`
- `proxy2vpn profile remove NAME`
- `proxy2vpn profile delete NAME`

### VPN services
- `proxy2vpn vpn add NAME --profile PROFILE [--port PORT] [--control-port PORT] [--location LOCATION]`
- `proxy2vpn vpn add --interactive`
- `proxy2vpn vpn list`
- `proxy2vpn vpn start [NAME | --all]`
- `proxy2vpn vpn stop [NAME | --all]`
- `proxy2vpn vpn restart [NAME | --all]`
- `proxy2vpn vpn update [NAME | --all]`
- `proxy2vpn vpn logs NAME [--lines N] [--follow]`
- `proxy2vpn vpn delete [NAME | --all]`
- `proxy2vpn vpn test NAME`

Notes:
- `vpn list` now includes health analysis by default; `--diagnose` and `--ips-only` options were removed.
- Provider is inferred from the selected profile during `vpn add`.
- `vpn start` starts an existing container or creates it if missing.
- `vpn restart` restarts containers in place.
- `vpn update` is the explicit command that pulls, recreates, and restarts containers.

### Server database
- `proxy2vpn servers update`
- `proxy2vpn servers list-providers`
- `proxy2vpn servers list-countries PROVIDER`
- `proxy2vpn servers list-cities PROVIDER COUNTRY`
- `proxy2vpn servers validate-location PROVIDER LOCATION`

### Fleet management
- `proxy2vpn fleet plan --countries "Germany,France" --profiles "acc1:2,acc2:8" [--output PLAN_FILE] [--unique-ips]`
- `proxy2vpn fleet deploy [--plan-file PLAN_FILE] [--parallel] [--validate-first] [--dry-run] [--force]`
- `proxy2vpn fleet status [--format table|json|yaml] [--show-allocation] [--show-health]`
- `proxy2vpn fleet rotate [--country COUNTRY] [--criteria random|performance|load] [--dry-run]`
- `proxy2vpn fleet scale up|down [--countries COUNTRIES] [--factor N]`

## Agent evidence privacy and migration

The watchdog sanitizes stored state, incident history, LLM requests and generated investigation results. It retains allowlisted diagnostic fields and check classifications, replaces raw log lines with recognized diagnostic facts, and omits raw exception messages. Live configuration and health objects used for recovery are not edited. Credential-overlapping service names use stable HMAC aliases in stored evidence and LLM requests; current configuration restores their live identity when history is loaded for recovery. The random local key is kept in `.proxy2vpn-agent/identity.key` with 0600 permissions; retain it for correlation and exclude it from evidence exports. Known legacy unsalted aliases are migrated, and unknown legacy aliases are redacted. Removed names with keyed aliases remain aliases until configured again.

Configured credentials are discovered in sensitive process environment fields, Compose environment and referenced env files (including shared profile anchors and custom filenames), the root `.env`, `profiles/*.env`, the configured external-proxy inventory, and compose-root control client/server credentials. Sensitive Compose expressions resolve shell variables over project `.env` values before matching, including defaults, alternatives, required and nested forms, and literal dollar escapes. Single-quoted dotenv values stay literal with either delimiter, and Compose raw env files retain quotes and dollar expressions without interpolation; Malformed expressions fail closed. YAML scalar quoting follows Compose interpolation, while dotenv single quotes stay literal. Numeric and boolean Compose credentials use the same string conversion as runtime environment parsing. Matching covers literal values, one layer of canonical URL/form encoding, padded standard or URL-safe Base64, and JSON string escaping. External `username_env` and `password_env` references resolve credentials even for names such as `LOGIN` or `KEY`. Recognized control classifications and valid timestamps are retained as diagnostic facts when their text coincides with a credential; narrative text remains redacted. Labelled passwords, tokens, authorization values, private keys and credential URL userinfo also use the shared logging redactor.

Existing `state.json` is scrubbed on the first state read; `incidents.jsonl` is scrubbed in full on the first history read or append, including superseded records. Run `proxy2vpn agent status` and `proxy2vpn agent incidents --all` after upgrading, for each compose root, while the old credentials are still available in configuration. Rewrites use an independent storage lock and atomic replacement; historical records are preserved. Invalid records or unreadable configuration stop the operation instead of forwarding unsafe evidence. Old binaries must be stopped before migration because they do not honor the new storage lock.

The guarantee cannot identify arbitrary unlabelled secrets in narrative text or credentials already removed from configuration. Raw logs and issue payloads that cannot meet the contract are omitted. Repeated/nested encodings, encryption, shell command evaluation and externally supplied Compose `--env-file` overrides are unsupported. Existing backups, exported evidence and daemon logs are outside this migration; handle them separately under your retention policy. See `lat.md/agent.md` for the full contract.

Agent storage requires a dedicated directory: unrelated files or subdirectories cause an error before permissions change. Configured filenames must avoid Windows device names, reserved characters, and Unicode/case aliases. Recognized crash temporaries are kept for safety.

Agent storage now uses 0700 directories and 0600 files on POSIX, repairs older permissions, and rejects symlinks, hard links and unsafe filenames. Storage transactions time out after ten seconds; a conflict means another writer changed the evidence, so reload before retrying. Continuous watchdogs reload and resume on the next interval. Stop old binaries before upgrading; they do not participate in revision checks. Windows uses inherited account ACLs rather than POSIX modes.

Manual approval records a persistent execution claim before rotation. If it is interrupted, the incident stays open but a repeat approval is rejected: inspect the recorded action and current service, reconcile any completed change, then dismiss or resolve the incident before planning a new operation. Claims never expire automatically.

Monitoring reset increments a generation and clears history through a recoverable private journal. Delayed work from before reset cannot repopulate the cleared records. An interrupted replacement retains the previous file or finishes a pending reset on the next access. Only a truncated final JSONL record is recovered automatically; other corruption requires operator repair, with the original file left intact. Incident history is rewritten atomically without pruning, so large histories increase write cost. Preserve the identity key for correlation.

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
# Run the supported full suite target
make test

# Or invoke pytest the same way the Makefile does
uv run --with pytest,pytest-xdist pytest -n auto
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

### Maintainer Releases
Maintainers can cut a release entirely from GitHub:

The `Prepare GitHub Release` workflow is the only supported release entry point. Do not create tags or GitHub Releases manually, and do not publish directly to PyPI.

1. Run the `Prepare GitHub Release` workflow from the Actions tab on `main`.
2. Enter the target version without the leading `v` (for example `0.16.0`).
3. The workflow runs checks, bumps `pyproject.toml` and `uv.lock`, builds `CHANGELOG.md`, pushes the release commit and tag, and creates a draft GitHub Release with the matching notes.
4. Review the draft in GitHub Releases and click `Publish release`.
5. Publishing the GitHub Release triggers the PyPI publish workflow.

PyPI publishing accepts only a published GitHub Release, validates that its tag version matches both `pyproject.toml` and `CHANGELOG.md`, and builds from that exact tag. Repeated delivery is idempotent, but a package cannot be published ahead of its GitHub Release.

Recent highlights (see CHANGELOG.md for details):
- `vpn add` is the single compose-only service-definition command.
- `vpn update` is the explicit recreate-and-refresh command for VPN containers.
- Profile lifecycle split: `profile remove` (from compose) and `profile delete` (delete env file).
- Control authentication is generated during `system init`, mounted automatically, and uses separate monitor/operator credentials for localhost-bound controls.
- Default health analysis in `vpn list`; removed `--diagnose`/`--ips-only` flags.

---

## Why Proxy2VPN Works

**Infrastructure as Code**: Treat VPN endpoints like any other infrastructure - version controlled, reproducible, and scalable.

**Built for Developers**: No GUI nonsense. Pure command-line interface that integrates with your existing workflows, CI/CD pipelines, and Docker toolchain.

**Production Ready**: Used for large-scale web scraping operations, geo-distributed testing, and enterprise proxy infrastructure. Battle-tested reliability with automatic health monitoring.

**Zero Vendor Lock-in**: Works with 30+ VPN providers. Switch providers, add accounts, or migrate configurations without rewriting your setup.

**From Minutes to Milliseconds**: Stop spending hours configuring VPN clients. Get from zero to working proxy infrastructure in under 5 minutes.

**Scale When You Need**: Start with a single endpoint, scale to hundreds across dozens of countries when your requirements grow.

## Get Started Now

```bash
uvx proxy2vpn system init
uvx proxy2vpn --help
```

Join developers who've eliminated VPN configuration headaches and built reliable proxy infrastructure that just works.

## License

MIT
