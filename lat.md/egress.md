# Shared Egress Interface

A shared health seam describes configured endpoint identity, available evidence, and supported operations without requiring a container or control server.

[[src/proxy2vpn/core/egress.py#EgressAdapter]] exposes identity, capabilities, and asynchronous observation. [[src/proxy2vpn/core/egress.py#EndpointIdentity]] identifies a configured endpoint, independently of its changing exit IP. [[src/proxy2vpn/core/egress.py#EgressObservation]] carries source-appropriate score/class, diagnostics, availability, authentication acceptance, request connectivity, latency, and observed exit IP. Missing evidence is `None`, never inferred success.

[[src/proxy2vpn/core/egress.py#EgressCapabilities]] distinguishes tunnel restart, service restoration, endpoint replacement, session replacement, and requesting a different exit IP. Capability support means a request can be made, not that health has recovered or an exit-IP change was verified. Gluetun fleet replacement remains owned by fleet orchestration; the common interface intentionally contains only the health operations exercised by both adapters.

[[lat.md/health#Health Assessment]] adds same-profile peer evidence and projects legacy container/control fields for compatible callers. [[recovery-policy#Recovery Policy]] reads normalized availability, restart readiness, and capabilities rather than interpreting Docker or control-server evidence. Execution also enforces capabilities before entering an existing runtime/fleet operation.

# Gluetun Adapter

The Gluetun adapter translates existing runtime evidence and scoring into the common health result while retaining Docker and control evidence for legacy consumers.

[[src/proxy2vpn/adapters/egress.py#GluetunEgressAdapter]] wraps [[gluetun-runtime#Gluetun Runtime]]. Missing/stopped containers, diagnostic errors, score/classification rules, and tunnel readiness retain their existing meaning. Compose remains authoritative for profiles and VPN services, and upgrading requires no Compose migration.

# External Endpoint Configuration

External HTTP CONNECT endpoints use a separate JSON file next to the active Compose path, with stable ids and environment-variable credential references.

[[src/proxy2vpn/core/external_proxy.py#ExternalProxyEndpoint]] separates its stable id from [[src/proxy2vpn/core/external_proxy.py#ProxyConnection]] and [[src/proxy2vpn/core/external_proxy.py#CredentialReference]]. The default file is `external-proxies.json`; `PROXY2VPN_AGENT_EXTERNAL_PROXIES_FILE` overrides it for watchdog and fleet status. Relative paths resolve next to the active Compose path, not the current working directory.

The version-1 JSON object contains `version: 1` and an `endpoints` array. Each endpoint has `id`, `connection: {protocol: "http_connect", host, port}`, optional `credentials: {username_env, password_env}`, optional `expected_egress_ips`, and optional `probe_urls`. Defaults probe `https://ipinfo.io/ip` and `https://ifconfig.me/ip`. Probe URLs must use HTTPS, without embedded credentials or fragments; proxy hosts cannot contain URLs or credentials. Unknown fields, duplicate ids, unsupported protocols, invalid ports/IPs, and collisions with Compose service names are rejected.

[[src/proxy2vpn/adapters/external_proxy.py#load_external_endpoints]] validates configuration without echoing input values in errors. It reads references rather than storing credential values. Missing configuration preserves Compose-only behavior. External-only workspaces may omit Compose entirely; no file or synthetic service is generated.

# External Proxy Adapter

The first external adapter supports health probes through an HTTP CONNECT proxy, with optional HTTP Basic authentication and verified TLS to HTTPS probe targets.

[[src/proxy2vpn/adapters/external_proxy.py#ExternalProxyAdapter]] passes an explicit proxy on every request and disables environment proxy/netrc selection, cookies, and redirects. The proxy hostname is resolved locally; the HTTPS destination hostname is sent in CONNECT and resolved by the proxy. Failed proxy/upstream/TLS requests never retry through the host directly. Fallback probes use the same configured proxy. The proxy hop itself is HTTP; only the target TLS connection is encrypted by this protocol.

A successful probe requires a completed HTTPS request and a complete, bounded response containing a valid IP literal, validated by [[src/proxy2vpn/adapters/ip_utils.py#parse_ip_literal]]. HTTP 407 means rejected proxy authentication. Other proxy/upstream/transport failures report connectivity failure without guessing authentication or exit IP. Successful requests with missing/invalid IP evidence remain unknown and unhealthy. Anonymous access leaves authentication evidence unknown. Diagnostics discard response bodies and exception text so credential echoes cannot reach incidents or logs.

The observed IP need not differ from the host's IP: an external proxy may legitimately share an egress network. An optional IP allowlist defines the operator's expected egress identity; mismatch is unhealthy. Without an allowlist, healthy means the configured route completed a verified HTTPS observation, not proof of geography, carrier, anonymity, or vendor identity.

The adapter supplies no sticky-session guarantee, vendor session renewal, endpoint replacement, connectivity repair, or exit-IP change request. Each assessment creates a new client session and may observe a different vendor-selected exit IP. The endpoint id remains stable across observations and incident updates.

# Watchdog And Fleet Workflows

Configured external endpoints participate in status, health, and incident workflows while unsupported recovery requests are blocked before any Docker or fleet operation.

[[agent#Watchdog Cycle]] merges external configuration with Compose inventory. External-only cycles skip Docker cleanup; mixed cycles retain Compose-scoped cleanup. Unhealthy external endpoints create investigation incidents, preserve the same incident id across updates, honor dismissal, and resolve on healthy observations. Snapshots retain source, capabilities, health class, authentication/connectivity/IP evidence, and latency alongside existing fields.

Investigation uses external health evidence and operator-oriented repair steps without container logs, profile validation, or control probes. Incident source is persisted independently of current inventory; legacy external incident records infer their source from the endpoint-specific kind. Manual approval and the fleet execution entry point reject historical external replacement after configuration and snapshots disappear, including if Compose later reuses the name. Healthy observations and Gluetun rename migration only affect incidents from the same source. [[src/proxy2vpn/adapters/fleet_commands.py#fleet_status]] includes external endpoints and, with `--show-health`, uses the common assessor for mixed or external-only inventories. Deployment, allocation, and rotation commands continue to operate on Compose VPN services.

Tests are described in [[egress-tests#External Egress Tests]].
