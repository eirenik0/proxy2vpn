---
lat:
  require-code-mention: true
---
# Incident Retention Tests

Retention and atomic compaction bound historical versions without losing recovery evidence, dismissal suppression or concurrent updates; preview must leave evidence unchanged.

## Latest Evidence And Status Classification

Compaction retains latest append-order evidence, source identity, revisions and interrupted approval claims; open, approved and failed incidents survive indefinitely, while old resolved/dismissed incidents expire without rewriting state.

## Clock And Expiry Boundaries

Naive UTC and offset timestamps compare consistently; future timestamps and exact retention boundaries survive, zero means immediate eligibility after the boundary, and huge configured durations do not overflow clock arithmetic.

## Dismissal Suppression

A dismissed incident survives its inclusive cooldown boundary even with a shorter retention period, suppressing watchdog recreation until both retention and cooldown have expired.

## Preview Has No Evidence Side Effects

Counts-only preview creates no storage, key or lock, preserves legacy bytes and permissions, exports no narratives, and rejects unsafe file types without repairing them.

## Preview Corruption And Pending Recovery

Preview safely rejects corruption and pending redo recovery without mutation; an unterminated tail is excluded from its estimate and repaired only by an applying operation.

## Atomic Interruption And Redaction

Interruption before or after atomic history replacement leaves a valid old or compacted history; retries converge, private temporaries are removed, and retained legacy evidence is sanitized without raw backups.

## Stale Updates And Reset Fences

Compaction preserves retained revisions, rejects delayed updates to pruned incidents, and refuses a pre-reset observation session so stale work cannot resurrect cleared evidence.

## Concurrent Writer Preservation

A concurrent process updating incident evidence through the shared lock completes before compaction reads its snapshot; the resulting history retains that update with one latest record.

## Automatic Cycle Compaction

A watchdog cycle compacts before inventory or network work, and an applying compaction error clears persisted active-cycle progress through the existing failure path.

## Operator CLI And Configuration

The CLI offers counts-only JSON preview, applying compaction and a nonnegative retention-days override; environment defaults are respected and invalid negative retention is rejected.

## Legacy Revision Migration

Persisted legacy revision-zero records are promoted once before writer exposure; preview leaves them untouched, and a stale legacy reader cannot reopen a subsequently dismissed and pruned incident.
