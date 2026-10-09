---
lat:
  require-code-mention: true
---
# Live Security Tests

Opt-in, digest-pinned Gluetun containers validate actual authentication, publication and migration behavior; strict release execution fails unmet prerequisites rather than treating skips as passes.

## Generated Roles And Compose Scoping

Real monitor reads and operator mutations succeed, forbidden settings and monitor mutations are denied, missing/wrong/crossed credentials fail, and two Compose roots select their own generated client credentials.

## Publication Reachability

A separate bridge client calibrates private-address proxied HTTPS reachability, then verifies host localhost access and remote exclusion after recreation, followed by successful private publication again.

## Migration Recreation And Rollback

Legacy and custom auth remain live until recreation; backups and reruns preserve all three files, secured services authenticate and proxy, and rollback restores prior auth, exposure and absent-file state.

Legacy unauthenticated roles ignore supplied Basic credentials before recreation and after rollback. Custom basic roles reject generated credentials in those states; both modes reject old access after secure recreation.

## Harness Failure And Isolation Guards

Scratch migration preserves original backups and the distinct live-security type label, keeping fixtures outside production VPN selectors; failed Compose teardown still attempts removal of owned containers and networks.

## Strict Prerequisites And Private Failures

Strict execution fails unmet live prerequisites, and subprocess failures report only a fixed stage message without exposing captured credentials, logs or response bodies.

## Cleanup Failures Erase Private Inputs

Resource cleanup attempts every lab despite failures, removes private scratch inputs afterward, and reports both resource and sanitized file-deletion failures without losing either diagnostic.

## Independent Cleanup Stages

Container discovery, forced removal and each network-discovery failure are reported without skipping remaining independent cleanup phases, limiting leftover resources during daemon instability.

## Port Allocation Across Interfaces

Proxy and control ports are reserved together on all IPv4 interfaces before release, preventing duplicate selection and collisions with listeners bound only to the private address. Docker still detects subsequent external allocation races.
