# Deployment Security

New VPN deployments publish proxies on localhost and use compose-scoped authenticated control access; existing exposure and custom authentication require explicit migration.

## Control Authentication

Generated monitor credentials authorize status reads, while separate operator credentials authorize tunnel, DNS, and updater mutations without exposing credential-bearing settings.

[[src/proxy2vpn/core/security.py#ensure_control_auth]] creates `control-server-auth.toml` and `control-client-auth.json` beside the active Compose file. Each role has an independent random password. Both files and migration backups are written atomically with owner-only permissions. Initialization preserves existing files, including with `--force`; repeating explicit migration also preserves valid generated credentials.

Monitor routes contain only GET status, port-forward, and public-IP observations. Operator routes contain PUT tunnel, DNS, and updater operations. Neither role grants settings access or wildcard permissions. Current VPN and legacy OpenVPN route names are both included. New credentials are never printed, stored in Compose, or copied into container labels.

[[src/proxy2vpn/adapters/http_client.py#GluetunControlClient]] discovers generated credentials only for loopback controls. GET/HEAD requests use monitor credentials; mutation requests use operator credentials. Redirects are disabled. CLI commands use the active Compose path, runtime requests use the service ownership label, and diagnosis uses container ownership when available.

`GLUETUN_CONTROL_AUTH=user:password` or explicitly supplied client settings remain the override for custom server configurations. Custom or legacy files without the generated marker retain existing behavior. A generated marker requires a valid matching private client file; missing, malformed, symlinked, permissive, or mismatched credentials fail with a generic error before a request.

## Proxy Exposure

New service definitions default to an IPv4 localhost binding, while loading and rewriting existing Compose services preserves their published addresses.

[[src/proxy2vpn/core/models.py#VPNContainer]] validates the proxy bind address as an IP literal. [[src/proxy2vpn/core/models.py#VPNService#from_compose_service]] preserves explicit short-form, IPv6, and long-form addresses; an existing mapping with no host address retains its all-interface meaning. Docker creation and Compose serialization use the same address.

`vpn add --proxy-bind-address ADDRESS` selects an address per new service. Fleet configuration and deployment plans also carry `proxy_bind_address`. An explicit `0.0.0.0` publishes on all IPv4 interfaces; a private host address restricts publication to that interface. Probe URLs use the live published address, substituting loopback for wildcard binds, and bracket IPv6 addresses correctly.

Existing Compose host interpolation is preserved verbatim and serialized in long syntax to avoid confusing default-value colons with port separators. New configuration still requires an IP literal. Docker SDK creation rejects unresolved expressions before changing containers; it does not implement Compose environment resolution.

## Explicit Migration

The system security command prepares backed-up deployment files and tells the operator to recreate services; it never mutates running containers.

`system secure --proxy-bind-address ADDRESS` changes only proxy-port bindings, preserving unrelated ports and service fields. `--replace-control-auth` explicitly replaces legacy or custom authentication with generated roles and keeps private backups. Compose saving retains the existing backup contract.

Recreate services with `vpn update --all` after preparing the files. Existing clients must use the selected host address; choosing localhost removes remote access after recreation. Restore the Compose and both authentication backups together to roll back, then recreate services again. Initialization's `--force` affects Compose creation, not credential rotation.

This milestone protects local control authentication and publication defaults. It does not add TLS to client proxy traffic, redact incident/LLM payloads, or change external HTTP CONNECT behavior. See [[lat.md/security-tests#Deployment Security Tests]] for validation.

## Live Release Validation

Digest-pinned, isolated Docker tests verify real control roles, host publication, migration, recreation and rollback before release acceptance.

See [[live-security-tests#Live Security Tests]] and `docs/live-security.md`. Ordinary CI reports opt-in skips; the manually dispatched live-security workflow uses strict prerequisites and a dedicated VPN profile. The separate bridge probe targets the real host address and requires positive calibration before accepting localhost exclusion. Mac releases additionally require a LAN device or VM check. Recreation uses unique Compose projects, avoiding the SDK's shared network on existing fleets.
