---
lat:
  require-code-mention: true
---
# External Egress Tests

Controlled HTTP CONNECT and TLS servers verify protocol behavior, common evidence, and incident integration without a purchased external service.

## CONNECT DNS And Authentication

Verify destination DNS happens at the proxy, ambient proxy settings are ignored, HTTP Basic credentials reach only the proxy, and verified TLS yields IP and latency evidence.

## Authentication Failure And Redaction

Verify HTTP 407 produces explicit authentication failure and unknown IP without credential values in diagnostics, serialized configuration, or assessment logs.

## No Direct Fallback

Verify proxy unreachability, upstream rejection, and TLS failure cannot reach a target directly or report an observed exit IP.

## Unavailable Egress Evidence

Verify completed requests with empty, invalid, embedded, or oversized responses leave IP evidence unknown and do not echo response contents into diagnostics.

## Source Appropriate IP Rules

Verify external health uses request/IP evidence and an optional allowlist rather than treating a host-IP difference as universal proof of correct egress.

## Unsupported Operations

Verify restart, restore, endpoint replacement, session replacement, and different-IP requests fail explicitly without network or backend effects.

## Configuration And Credential References

Verify valid reference-only configuration, optional missing files, missing credentials, duplicate ids, invalid hosts/ports/protocols/probes/IPs, and safely redacted validation failures.

## Both Adapters Share Health Interface

Verify both concrete adapters expose the same health interface and mixed assessment preserves Gluetun evidence while external evidence does not require a container or control server.

## Capability Driven Recovery

Verify pure policy investigates unhealthy endpoints lacking repair capabilities even when contradictory legacy backend fields suggest readiness, and resolves healthy observations.

## Watchdog Identity And Incidents

Verify external-only watchdog cycles persist stable identity, update/investigate/dismiss/resolve incidents, report live recovery blocks, and perform no Docker/runtime actions.

## Fleet And Status CLI

Verify external-only fleet JSON includes source-appropriate health and agent run/status JSON persists endpoint source without creating Compose services.

## Mixed Inventory And Identity Collisions

Verify mixed inventory preserves Compose content and Gluetun checks, while watchdog and fleet reject external ids colliding with any Compose service name.

## Historical Unsupported Actions

Verify a removed endpoint's persisted identity still blocks manual/fleet replacement and investigation never falls back to Docker evidence.

## Batch Cancellation

Verify cancelling a batch cancels and joins outstanding external probes so their client/resource cleanup can complete.

## Real Watchdog Authentication Incident

Verify a real controlled proxy rejection flows through shared assessment into a watchdog investigation incident without Gluetun operations.
