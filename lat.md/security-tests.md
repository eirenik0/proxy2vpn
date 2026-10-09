---
lat:
  require-code-mention: true
---
# Deployment Security Tests

These tests verify authenticated control access, credential handling, binding compatibility, and explicit migration without changing existing deployment behavior implicitly.

## Generated Credentials

Generated role passwords are independent and private, and repeated initialization or migration preserves them to avoid disconnecting running controls.

## Legacy And Custom Authentication

Existing auth files remain intact until explicit replacement, which retains an owner-only backup for rollback.

## Invalid Credential Files

Missing, malformed, permissive, mismatched, and symlinked generated credentials fail without revealing passwords.

## Credential Write Rollback

A failed server-auth write restores the original auth file and removes newly written client credentials so bootstrap does not leave a mismatched pair.

## Authenticated Control Requests

Real local HTTP requests use monitor credentials for reads and operator credentials for writes; the monitor cannot authorize a mutation.

## Compose Scoped Clients

Two workspaces select different credentials, environment overrides remain supported, and automatic local credentials are never sent to remote controls.

## Runtime Compose Ownership

Watchdog control requests pass the endpoint's owning Compose path so authentication works independently of the shell working directory.

## Proxy Binding Compatibility

Existing implicit, explicit, private, IPv6, and long-form bindings survive model round trips while new endpoints default to localhost.

## Compose Binding Interpolation

Existing Compose host expressions survive listing, saving, and model round trips without being resolved against the process environment or accidentally bracketed as IPv6 addresses.

## Unresolved Binding Safety

New service input requires IP literals, and Docker creation rejects unresolved Compose expressions before accessing Docker or removing an existing container.

## Explicit Migration

Migration preserves unrelated service fields and ports, keeps backups, and initialization does not rotate generated credentials.

## Probe Bind Addresses

Health probes use explicit private or IPv6 bindings and select loopback for wildcard publication.

## Proxy Export Exposure

Exported endpoints report their actual local/private bind address instead of claiming a publicly reachable host for a localhost-only proxy.

## Diagnostic Bind Addresses

Log-based diagnostics probe the actual private interface binding so a healthy proxy is not reported broken merely because it does not listen on localhost.
