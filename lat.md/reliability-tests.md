---
lat:
  require-code-mention: true
---
# Reliability Tests

These regressions verify desired-state consistency, fleet resource ownership, and continuous monitoring across operational failures.

## Fleet Fresh Compose

A subsequent fleet operation reloads desired state after an independent writer commits, preserving that change and avoiding permanent conflicts from a cached manager.

## Compose Snapshot Bytes

The stale-write fingerprint uses the exact parsed or written bytes, so an external edit during load validation or immediately after replacement is rejected by a later save.

## Compose Stale Writers

Independent Compose snapshots cannot overwrite newer desired state or its backup; a stale writer receives an explicit conflict and must reload before retrying.

## Compose Direct Edits And Removal

Direct document edits use the same stale-write check as typed mutations, including external text changes and deletion that must not be silently undone.

## Compose Commit Failure

Failed atomic replacement preserves the last committed file and permits an explicit retry against the same unchanged revision.

## Scale Batch Reservations

Scale-up with repeated cities reserves distinct names and proxy/control ports for the complete batch before starting containers, so a later service cannot reuse an earlier binding.

## Scale Rejected Definition

When a generated name collides with an existing definition, rejected scale-up preserves that definition and its bindings instead of deleting it as failed-attempt cleanup.

## Scale Partial Commit Failure

If the first add in a scale-up batch fails during atomic replacement, a later successful add persists only its own started definition and retains only its port reservations.

## Plan Shared Host Ports

Deployment plans avoid existing bindings and reserve proxy and control ports in one host namespace, including overlapping requested ranges and unmanaged Compose mappings.

## Port Exhaustion

A batch that cannot reserve both port roles fails without partial reservation, preventing an out-of-range binding or leaked capacity after exhaustion.

## Forced Deployment Shared Network

Forced deployment preserves all existing shared-network attachments, including another Compose root and unmanaged containers, while replacing only the requested root's desired definitions.

## Watchdog Failure Then Recovery

A transient operational inventory failure persists a failed cycle and cleared progress, then a continuous watchdog reloads evidence and completes a later successful cycle, including after reset during backoff.

## Watchdog Bounded Backoff

Repeated operational failures use increasing delays capped at five minutes or the configured interval if longer; a successful cycle restores the ordinary interval and resets retry delay.

## Watchdog Fatal Failures

Storage integrity, disk, and invalid configuration failures propagate without retrying, preserving the fail-closed evidence contract.

## Watchdog Retry Cancellation

Cancellation during retry sleep exits promptly and preserves the completed failed cycle without publishing an extra attempt or active progress.

## One Shot Failure

One-shot monitoring retains its failure exit contract rather than silently retrying an operational failure.
