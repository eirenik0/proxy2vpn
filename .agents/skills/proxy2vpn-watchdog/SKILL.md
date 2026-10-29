---
name: proxy2vpn-watchdog
description: Implement or debug proxy2vpn watchdog recovery decisions, delayed health checks, incident transitions, action evidence, or concurrent agent persistence. For live fleet operations use the existing proxy2vpn-ops skill.
---

# Proxy2VPN watchdog changes

Read `lat.md/recovery-policy.md`, `lat.md/agent.md`, and the relevant sections of `lat.md/recovery-policy-tests.md` before changing recovery behavior. For persistence changes also read `lat.md/agent-storage-tests.md`; for evidence changes read `lat.md/agent-evidence-tests.md`.

## Find the failing transition

Use pstack's `principle-fix-root-causes` when available. Reproduce the failure with supplied observations, explicit time/settings, and controlled runtime results. Locate the decision in `src/proxy2vpn/agent/recovery_policy.py`, its execution in `agent/runtime.py`, and its persisted outcome in `agent/state.py` and `agent/models.py`.

Keep policy free of Docker, network calls, filesystem reads, environment reads, sleeping, clock reads, and input mutation. Put effects and delayed observations in orchestration. Use the existing injected runtime interfaces in `adapters/gluetun_runtime.py`.

## Evidence and concurrency contracts

- Request acceptance does not prove recovery. Restart and restore need delayed health observations; an IP-change result needs healthy connectivity and known, different before/after addresses.
- Preserve restart/restore/rotation ordering, cooldowns, degradation timing, and scope breakers unless the requested change explicitly alters them.
- Carry requested and final service names through successful, failed, interrupted, and rolled-back rotation. A failed operation must not fabricate healthy state or resolve an incident.
- Gate recovery by endpoint capabilities and source. External HTTP CONNECT endpoints cannot acquire Docker repair operations through a reused service name.
- Recheck current incident state before updates. Preserve dismissal, reset generation fences, stale-write rejection, and approval claims. Hold no storage transaction across network work.
- Pass evidence through the existing sanitizer before persistence or LLM requests. Keep raw credentials and response bodies out of incidents and logs.

## Prove the transition

Add a regression at the decision or execution boundary that reproduces the failure. For timing, cancellation, or concurrent writers, assert the resulting action evidence and persisted incident state as well as the returned decision. Link each new required test spec once beside its covering test.

Use `$proxy2vpn-verify` with `recovery`, `storage`, or both. Broaden to `egress`, `fleet`, or `runtime` when the change crosses those boundaries. Update the affected lat.md contract and run `lat check`.

For a production incident, load `.codex/skills/proxy2vpn-ops/SKILL.md` and follow its live-state workflow. A code-change request alone does not authorize a fleet mutation.
