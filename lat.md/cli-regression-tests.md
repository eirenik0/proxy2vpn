---
lat:
  require-code-mention: true
---
# CLI Regression Matrix

Cross-command regression checks verify CLI entry points, atomic rejection of malformed profiles, and accurate lifecycle exit status without requiring VPN credentials.

## Command Help

Every registered command and group must render help successfully, including nested entry points, so CLI dependency or command registration changes cannot silently break discovery.

## Malformed Profiles

Empty, incomplete and invalid profile files must return a controlled failure without changing compose or the input file, preventing partial profile creation after validation errors.

## Partial Restart Failure

A bulk restart must attempt remaining services after a failure and return a nonzero exit code, so automation cannot mistake a partially failed lifecycle operation for success.

## Failed Restoration

Restoration must return a nonzero exit code when restart and recreation fail, preserving the failure summary so scripts can distinguish unsuccessful recovery from a healthy service.

## Canonical Control Routes

Control endpoint mappings use the canonical port-forward route so redirect rejection does not cause a JSON decoding error against current Gluetun servers.

## Unavailable Public IP

An empty control API public IP must produce a clear nonzero CLI result rather than an empty successful response when a tunnel has not connected.

## Structured Fleet Health

Managed-only fleet JSON and YAML must parse with default allocation display and optional health checks; health evidence belongs in the structured result and probe progress must not contaminate stdout.

## Delete Missing Containers

Deleting a service after stop has removed its container must succeed even when Docker not-found errors are wrapped; other Docker failures must preserve the compose definition.

## Referenced Profile Removal

Removing a referenced profile must fail without changing compose, even with confirmation bypassed. Usage includes YAML merge anchors when profile labels are missing or stale.
