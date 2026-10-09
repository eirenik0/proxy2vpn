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

Verify external-only fleet JSON remains parseable with default allocation options and long authentication diagnostics, while agent run/status JSON persists endpoint source without creating Compose services.

## Mixed Inventory And Identity Collisions

Verify mixed inventory preserves Compose content and Gluetun checks, while watchdog and fleet reject external ids colliding with any Compose service name.

## Historical Unsupported Actions

Verify external incidents retain their source after removal and Compose name reuse; investigation and approval remain external, and removed endpoints without a current Compose identity cannot enter fleet execution.

## Batch Cancellation

Verify cancelling a batch cancels and joins outstanding external probes so their client/resource cleanup can complete.

## Real Watchdog Authentication Incident

Verify a real controlled proxy rejection flows through shared assessment into a watchdog investigation incident without Gluetun operations.

## Upstream HTTP Failures

Verify HTTP failures inside the target TLS connection do not become proxy-authentication failures or erase the evidence that CONNECT accepted the configured credentials.

## Source Change Resets Recovery History

Verify snapshots discard failure counts, degradation age, and prior action fields when endpoint source changes, while same-source observations retain their episode history.

## New Gluetun First Cycle

Verify a Gluetun service reusing an external endpoint name gets its normal first-cycle restart without stale restoration/rotation, while the external incident remains separate.

## Gluetun Rotation After Source Reuse

Verify current Gluetun services can rotate automatically and by approval despite open, resolved, or dismissed external incidents with the same name, while approval of those historical external incidents remains blocked.

## Investigation Uses Persisted Source

Verify historical Gluetun incidents keep their investigation backend when an external endpoint or snapshot reuses the name, without probing the unrelated proxy or borrowing its health evidence.

## External Incident Action Evidence

Verify external incident enrichment and investigation omit historical Gluetun restart, restore, and rotation evidence after name reuse, while retaining the audit history in persisted state.

## Shared Provider Bucket

Verify external fleet entries extend an existing Compose provider bucket named external_proxy, preserving service counts and source-specific health in JSON, YAML, and table output.

## Provider Label Is Not Endpoint Source

Verify a Gluetun service labeled with provider external_proxy retains Gluetun investigation guidance, since provider grouping does not establish endpoint source.
