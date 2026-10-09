# Operational Logging

Operational logs share one redacted JSON contract across stdlib callers, third-party libraries, and structlog events, while CLI command output stays independent.

## Event Schema

Each record carries a UTC ISO timestamp, uppercase level, logger name, and identical event and message strings so existing message consumers remain compatible.

Required fields are `timestamp` (ISO 8601 ending in `Z`), `level` (such as `INFO` or `ERROR`), `logger` (the module or library name), `event` (event name or formatted message), and `message` (a compatibility alias of `event`). Existing stdlib `extra=` fields remain top-level fields. New events use keyword fields and bound loggers through [[src/proxy2vpn/adapters/logging_utils.py#get_event_logger]]; existing callers retain [[src/proxy2vpn/adapters/logging_utils.py#get_logger]].

Rendered tracebacks appear in `exception`, with the legacy `exc_info` alias preserving existing exception consumers. Optional operational fields include `compose_path`, `cycle_id`, `service_name`, `provider`, `incident_id`, `incident_type`, `health_class`, `health_score`, `failing_checks`, `action`, `trigger`, `result`, and `details`. Internal formatter metadata never reaches JSON. New consumers may use `event`; existing consumers need no migration.

## Processor Pipeline

Structlog and stdlib records converge on one ProcessorFormatter before rendering, giving legacy and new callers the same schema without additional handlers or duplicate records.

[[src/proxy2vpn/adapters/logging_utils.py#configure_logging]] configures structlog's stdlib logger factory and BoundLogger. Native calls use stdlib level filtering and positional argument formatting. The shared processors merge task-local context, add level and logger name, and stamp UTC time. Stdlib records first use structlog's ExtraAdder to preserve `extra=` fields.

Both paths pass through ProcessorFormatter metadata removal, stack rendering, exception formatting, schema normalization, [[src/proxy2vpn/adapters/logging_utils.py#redact_secrets]], and JSONRenderer, in that order. Exception text is redacted after traceback rendering and before serialization. The root logger owns exactly one output handler; reconfiguration closes the previous proxy2vpn handler. Foreign handlers removed from the root are not closed because their owner may still need them.

`--log-file` selects a JSON file handler. Without it, a NullHandler suppresses logs so normal and machine-readable command output stay quiet. The daemon child receives its compose-root `daemon.log` through the same option. [[src/proxy2vpn/adapters/logging_utils.py#set_log_level]] changes the root level for existing loggers from both APIs; no fixed-level structlog wrapper bypasses that change.

The integration follows the upstream [stdlib ProcessorFormatter pattern](https://www.structlog.org/en/stable/standard-library.html) and [contextvars guidance](https://www.structlog.org/en/stable/contextvars.html).

## Context Boundaries

Task-local scopes carry cycle, service, and provider context through async work and asyncio.to_thread without contaminating concurrent checks or later operations.

[[src/proxy2vpn/adapters/logging_utils.py#logging_context]] restores the complete caller context in `finally`, including failures and cancellation. CLI logging setup clears stale context. Each [[lat.md/agent#Watchdog Cycle]] starts a clean scope with a unique `cycle_id` and `compose_path`; service processing adds `service_name` and `provider` only within its scope.

[[src/proxy2vpn/core/services/health_assessment.py#HealthAssessmentService#assess_service]] scopes standalone checks, and batch worker scopes also cover overridden assessors and failure logs. Child tasks inherit the cycle context and bind their own service fields; progress callbacks in the parent cannot inherit child service fields. Thread probes launched through asyncio.to_thread receive the caller's context.

Watchdog cycle start, completion, and failure events correlate with health assessment completion or failure and action completion. Incident open, update, and resolution events bind their specific `incident_id` without leaving it on later services or cycle summaries. Fields are emitted where available rather than inventing provider or incident identifiers.

## Secret Redaction

Central redaction masks credential URL userinfo and labelled secrets in text, recursively masks sensitive fields, and runs on rendered exceptions before JSON serialization.

Sensitive field names are case-insensitive and normalize punctuation, covering password, passwd, passphrase, token, API key, secret, credential, authorization, private key, access key, pass, and pwd variants. The entire value of a sensitive field is replaced with `[REDACTED]`, including nested objects. Non-sensitive mappings and collections are copied recursively, bytes are decoded, and custom values become text before redaction so later JSON fallbacks cannot expose their original representation. Nesting beyond twenty levels is replaced with `[TRUNCATED]` to bound cycles.

Text matching covers scheme-based URLs with userinfo (including HTTP and SOCKS proxy credentials), sensitive key assignments with `:` or `=`, quoted values, query parameters, and Bearer or Basic authorization values, plus complete PEM private-key blocks. It preserves useful non-secret fields such as provider, port, and health classifications. Arbitrary unlabelled secrets cannot be inferred; callers should use sensitive structured fields for credentials and avoid logging raw payloads. This log processor does not redact CLI result data. Persisted agent state and LLM payloads use the separate [[agent#Evidence Secrecy Contract]] and share the pattern matcher.

See [[lat.md/logging-tests#Logging Tests]] for contract and regression coverage.

Interrupted or failed restart rechecks emit `agent_recovery_observation_failed` with the service, action, terminal result, and runtime request outcome so request acceptance is distinguishable from observed recovery.
