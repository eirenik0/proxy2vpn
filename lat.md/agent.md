# Watchdog Cycle

The watchdog runs one remediation policy loop per compose root and persists progress often enough for status commands to observe live work.

[[src/proxy2vpn/agent/runtime.py#AgentWatchdog#run_cycle]] loads the compose-root state, removes orphaned containers, batch-assesses services, updates per-service snapshots, and then applies bounded remediation such as tunnel restart, service restore, or fleet rotation. The cycle uses the shared health-assessment layer from [[lat.md/health#Health Assessment]] rather than duplicating probe logic.

Cycle, service, action, and incident events follow [[lat.md/logging#Operational Logging]]. Each cycle has a unique ID, concurrent checks isolate service/provider context, and incident events bind their IDs without contaminating later services.

Runtime inspection, incident evidence, control requests, restoration, and orphan cleanup use [[lat.md/gluetun-runtime#Gluetun Runtime]]. The watchdog retains recovery execution, rechecks, timing, and action history; deterministic decisions use [[recovery-policy#Recovery Policy]]; its shared assessor receives the same injected runtime instance.

[[egress#Watchdog And Fleet Workflows]] adds external endpoint inventory and investigation-only incidents. External-only roots need no Docker or Compose file. Capabilities block unsupported recovery before execution, while existing Gluetun actions and state remain compatible.

# Incidents And State

Agent state is stored next to the compose file so watchdog status, daemon metadata, and incident history move with the workspace.

[[src/proxy2vpn/agent/state.py#AgentStateStore]] persists the current watchdog status, service snapshots, and append-only incident records under the compose root. This keeps `agent status`, daemon supervision, and post-incident investigations aligned with the exact compose file the watchdog was monitoring.

Incident records persist their endpoint source independently of transient snapshots. Historical external incidents cannot gain Gluetun capabilities after removal or name reuse; healthy evidence and rename migration preserve source boundaries.

# Evidence Secrecy Contract

Agent evidence uses copied, allowlisted diagnostics at persistence and LLM boundaries, leaving live health results and configuration intact for recovery.

[[src/proxy2vpn/agent/evidence.py#EvidenceSanitizer]] shares labelled-secret and credential-URL redaction with operational logging through [[src/proxy2vpn/core/redaction.py#_redact_text]]. Recognized typed classifications and valid timestamps retain their factual values even when coincidentally equal to credentials; unknown control strings and narrative text remain redacted. Only known diagnostic fields survive; issue payloads become check classifications, boolean/unknown outcomes, and fixed messages. Enrichment fallbacks are built from these classifications. Raw log lines become at most six recognized literal facts. Unknown objects and payload keys are omitted. Text is bounded to 2048 characters, diagnostic lists to 64 entries, and recursion to twenty levels; service and action histories retain their full lists for recovery.

Configured values come from sensitive process environment fields, Compose environment, Compose service and profile-anchor env_file references (any filename), compose-root .env, profiles/*.env, the configured external-proxy JSON inventory, and compose-root control client/server credentials, including URL userinfo. External username_env/password_env references explicitly resolve environment values regardless of variable names. Standard shell USER, USERNAME, PWD and OLDPWD metadata is excluded from process credential discovery. Sensitive Compose values resolve braced/unbraced interpolation using shell variables over project dotenv values, including defaults, alternatives, required forms, nested expressions and dollar escapes. Single-quoted dotenv values remain literal with equals or colon delimiters. Env-file format raw preserves quotes and dollar expressions without interpolation; files shared by raw and standard references collect both representations. malformed expressions fail closed. See [[src/proxy2vpn/agent/interpolation.py#interpolate]] and [Compose interpolation](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/).

Non-null scalar credential values use the runtime string representation, including numeric and boolean Compose environment entries. Credential matching supports literal values, one layer of canonical URL percent encoding or form encoding, padded standard/URL-safe Base64, and JSON string escaping. Full credentials containing `[REDACTED]` match before standalone markers; bounded redaction converges so replacements cannot leave a newly formed configured credential behind. Sensitive keys cover the logging contract; configured username, user, and OPENVPN_USER values are also collected.

[[src/proxy2vpn/agent/llm.py#OpenAIIncidentEnricher]] and [[src/proxy2vpn/agent/llm.py#OpenAIIncidentInvestigator]] sanitize before serialization and sanitize generated results. The watchdog also sanitizes disabled and failed-call fallbacks, and storage sanitizes status errors, action details, incident summaries and investigation results. Raw exception strings become a fixed diagnostic-error marker (with the literal cancellation classification preserved); result and action classifications remain available.

Service identity fields overlapping credentials use deterministic SHA-256 aliases in storage and LLM payloads. The state store restores aliases to current configured Compose service names and external endpoint IDs only for in-memory recovery correlation. Removed identities retain their alias until configuration restores them; aliases cannot repair names already replaced irreversibly by old redactors. Generated twelve-hex-digit incident IDs retain their control identity.

[[src/proxy2vpn/agent/state.py#AgentStateStore]] scrubs state when read and every historical incident record before loading or appending. A separate evidence.lock serializes history rewriting and append, independently of the long-lived watchdog runtime lock. Unique temporary files are flushed and atomically replaced; all historical records remain in order. Malformed records or unreadable configuration fail closed rather than reaching prompts. No backup containing unsanitized evidence is created.

Arbitrary unlabelled secrets in retained narrative text cannot be inferred. Unrecognized raw logs and issue payloads are excluded, but summaries and generated prose retain bounded, pattern-redacted text. Removed credentials no longer available in configuration are covered only by supported patterns. Nested/repeated encodings, encrypted values, shell command evaluation and externally supplied Compose --env-file overrides are outside the configured-value contract. Previously exported files, backups and daemon logs are not migrated by the state store.

See [[agent-evidence-tests#Agent Evidence Tests]] for regression coverage and the README agent evidence section for operator migration guidance.
