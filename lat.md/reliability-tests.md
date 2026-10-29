---
lat:
  require-code-mention: true
---
# Reliability Tests

These regressions verify desired-state consistency, fleet resource ownership, and continuous monitoring across operational failures.

## Fleet Fresh Compose

A subsequent fleet operation reloads desired state after an independent writer commits, preserving that change and avoiding permanent conflicts from a cached manager.

## Compose Stale Writers

Independent Compose snapshots cannot overwrite newer desired state or its backup; a stale writer receives an explicit conflict and must reload before retrying.

## Compose Direct Edits And Removal

Direct document edits use the same stale-write check as typed mutations, including external text changes and deletion that must not be silently undone.

## Compose Commit Failure

Failed atomic replacement preserves the last committed file and permits an explicit retry against the same unchanged revision.

## Scale Batch Reservations

Scale-up with repeated cities reserves distinct names and proxy/control ports for the complete batch before starting containers, so a later service cannot reuse an earlier binding.

## Plan Shared Host Ports

Deployment plans avoid existing bindings and reserve proxy and control ports in one host namespace, including overlapping requested ranges and unmanaged Compose mappings.

## Port Exhaustion

A batch that cannot reserve both port roles fails without partial reservation, preventing an out-of-range binding or leaked capacity after exhaustion.
