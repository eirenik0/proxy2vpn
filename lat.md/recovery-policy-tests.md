---
lat:
  require-code-mention: true
---
# Recovery Policy Tests

These tests isolate deterministic policy choices from runtime execution and verify compatibility through existing watchdog, CLI, compose, fleet, and persistence regressions.

## Pure Evaluation

Repeated evaluation with explicit time returns the same decision without input mutation or filesystem, environment, network, clock, or sleeping access.

## Initial And Transient Recovery

Healthy observations resolve; missing/stopped services restore; first-cycle running failures restart when control is reachable, and a healthy delayed recheck ends recovery.

## Authentication And Configuration

Only isolated persistent auth failure with healthy profile peers, reachable control, and no active auth incident restarts; shared auth and configuration failures require investigation.

## Route And TLS Escalation

Persistent route failure restores once and rotates on the next unhealthy cycle; post-restart TLS failure skips restore, while route failure newly seen after restore retains grace.

## Time Windows And Budgets

Explicit-time boundary tests preserve restore cooldown and rotation grace comparisons, successful rotation budget windows, manual/failure exclusions, and requested/final service identity.

## Scoped Circuit Breakers

Profile and provider/country breakers consume matching normalized peers or recent active scope incidents; unrelated providers and countries do not suppress rotation.

## Dismissal And Approval

Dismissed incidents remain suppressed through the cooldown boundary; explicit manual approval permits rotation and closed or non-rotation incidents remain rejected.

## Connectivity Versus Exit IP

Connectivity recovery can retain an IP, but confirming a different exit IP requires healthy observations with known and different before/after addresses.

## Interrupted Runtime Actions

Cancelling tunnel restart, recreation, or its recheck preserves open incidents and unhealthy state, records a failed attempt, clears progress, and keeps restore cooldown active.

## Rotation Outcomes And Identity

Successful, failed, cancelled, and raised rotation outcomes preserve requested/final identity and incident history; only successful rotation marks connectivity recovered and resolves migrated incidents.

## Interrupted Manual Approval

A cancelled approved rotation retains its open approval-required incident and failed action after rename, without losing the snapshot identity or leaving active progress behind.
