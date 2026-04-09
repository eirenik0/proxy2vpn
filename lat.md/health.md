# Diagnostic Signals

Diagnostics combine recent log evidence with live proxy connectivity so recovery logic favors current behavior over stale container history.

[[src/proxy2vpn/core/services/diagnostics.py#DiagnosticAnalyzer]] inspects the newest log slice for server-selection failures, authentication problems, TLS issues, DNS failures, route setup errors, and configuration errors. When a proxy port is available it also compares the direct public IP with the proxied IP to confirm the tunnel is actually changing egress.

# Health Assessment

Health assessment wraps diagnostics with container state, control API reachability, and peer evidence so higher-level workflows get one normalized result object.

[[src/proxy2vpn/core/services/health_assessment.py#HealthAssessmentService]] turns one [[src/proxy2vpn/core/models.py#VPNService]] into a [[src/proxy2vpn/core/services/health_assessment.py#HealthAssessment]] by checking container presence, log diagnostics, control API status, and current egress IP. Batch assessment enriches each result with same-profile peer evidence so the watchdog can distinguish isolated auth failures from broader provider issues.

# Provider-Scoped Rotation Memory

Rotation memory is scoped by provider and location so one provider's failures do not poison candidate selection for another provider in the same city.

[[src/proxy2vpn/adapters/server_monitor.py#ServerMonitor]] caches the last health result per provider/location pair and tracks recent rotation failures with the same scope. That memory feeds rotation planning, allowing multi-provider fleets to treat `Toronto` for ExpressVPN and `Toronto` for NordVPN as separate candidates instead of one shared bucket.
