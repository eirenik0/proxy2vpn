# Recovery Policy

Recovery policy chooses the next bounded watchdog step from supplied health, scope, history, settings, and time; orchestration performs effects and persists their outcomes.

[[src/proxy2vpn/agent/recovery_policy.py#RecoveryPolicy]] evaluates [[src/proxy2vpn/agent/recovery_policy.py#RecoveryContext]] without Docker, HTTP, compose reads, environment reads, sleeping, clock reads, or input mutation. ComposeManager resolves service/profile/provider/country identities before evaluation. Settings are explicit values in [[src/proxy2vpn/agent/recovery_policy.py#RecoverySettings]], rather than an environment-backed settings object.

## Decisions And Observations

Each decision names a supported action or terminal outcome, explains the reason, and specifies the evidence required after execution.

[[src/proxy2vpn/agent/recovery_policy.py#RecoveryDecision]] returns resolve, restart_tunnel, restore, rotate, incident, or wait. Restart and restore require a delayed health observation; the fleet workflow already verifies connectivity after rotation. A healthy observation resolves incidents, while accepting a runtime request alone does not establish recovery.

[[src/proxy2vpn/agent/recovery_policy.py#FollowupObservation]] distinguishes repairing connectivity from requesting a different exit IP. Connectivity may recover with the same IP. An IP-change request requires healthy connectivity plus known, different before/after addresses; attempting or completing rotation alone cannot supply that evidence. Existing watchdog/manual incident rotation repairs connectivity, and action details record an unknown IP-change outcome rather than inventing a changed address. This refactor adds no automatic action or CLI command.

## Bounded Recovery And Limits

The policy retains the existing restart, restore, and rotation sequence, including isolated-auth recovery, timing gates, and scoped rotation breakers.

Initial healthy evidence resolves incidents. Persistent authentication/configuration evidence produces an investigation incident; an isolated authentication failure with healthy profile peers and a reachable control API may restart once unless an active auth incident already exists. Configuration failures and shared auth failures do not take that restart path.

Service failure decisions use the latest diagnostics, including delayed rechecks. New persistent auth/config evidence immediately requests investigation, and cleared initial authentication evidence does not override current connectivity failures.

Other first-cycle running failures restart when control is reachable. An unhealthy recheck ordinarily attempts restoration unless its cooldown is active. TLS failure after restart skips restoration. Persistent route/connectivity failure gets one restoration and waits until the next cycle before escalating; route failure first observed after restoration retains the ordinary grace period.

Restore cooldowns count successful and failed attempts, including interrupted restores. Rotation grace uses the persisted degradation timestamp. Automatic rotation budgets retain their successful-action windows: one in 30 minutes or two in six hours, including requested/final rename identity. Profile auth/config breakers require two matching assessments or an active one-hour scope incident. Provider/country breakers require two other non-auth unhealthy peers or an active 30-minute scope incident.

Dismissal suppresses reopening a matching incident for its configured cooldown. Existing active scope incidents still update across matching services. [[src/proxy2vpn/agent/recovery_policy.py#RecoveryPolicy#manual_rotation]] validates the existing explicit manual approval path, rejects closed/non-rotation incidents, and preserves the operator's ability to override automatic budgets.

## Execution And Persistence

The watchdog owns runtime calls, delayed rechecks, fleet mutation, progress, incident transitions, and compatible persisted models.

[[src/proxy2vpn/agent/runtime.py#AgentWatchdog#_process_service]] passes each follow-up observation through policy again, allowing at most restart, restore, and rotation in one cycle. [[lat.md/gluetun-runtime#Gluetun Runtime]] supplies runtime operations; [[lat.md/fleet#Rotation Execution]] supplies fleet execution. No policy callback performs an effect.

Interrupted actions propagate cancellation, record failed attempts, retain current unhealthy observations and incidents, and clear active cycle progress. A cancelled or failed restart recheck marks its action failed while retaining the runtime request outcome in details. Known persistent auth evidence still persists an investigation incident. Interrupted attempt history prevents another isolated restart in the same degradation episode even when dismissal suppresses that incident; a later episode may restart again. Rotation progress follows both candidate rename and rollback identity. When failed results omit rotation metadata, the latest live progress name supplies final action identity and incident migration for both automatic and approved rotations. Failed or interrupted rotations retain known requested/final names without marking a service healthy or resolving its incidents. Successful rotation migrates incidents before resolving them under the final name. Manual approval retains its existing terminal incident history and migrates other active incidents.

[[src/proxy2vpn/agent/models.py#AgentState]], [[src/proxy2vpn/agent/models.py#ActionRecord]], and [[src/proxy2vpn/agent/models.py#AgentIncident]] retain their persisted schemas; extra rotation details use the existing string map. Tests are specified in [[recovery-policy-tests#Recovery Policy Tests]].

# Egress Capabilities

Recovery decisions use explicit supported operations and normalized readiness so endpoint health does not imply Docker or control-server availability.

[[egress#Shared Egress Interface]] supplies capabilities through resolved service identity. Unhealthy endpoints with no supported repair operation produce investigation incidents rather than restart, restore, or rotation. Backend-specific readiness is normalized by adapters outside the pure policy; existing Gluetun limits and action order remain intact.

Snapshot episode history is retained only when the prior snapshot source matches current assessment source. A source change starts fresh failure timing and action fields, preserving first-cycle Gluetun recovery even when names are reused.
