# Operator Monitoring

Provisioned dashboards, tested Prometheus alerts and runbooks expose known health separately from absent, unknown and stale monitoring evidence.

## Alert Evidence

Sustained failures retain the latest fresh boolean sample over a bounded three-minute window, bridging active-cycle gaps while fresh success immediately clears held failure.

The default five-minute hold suppresses transient probes. Independent unknown/stale alerts describe monitoring gaps. Current open/approved rotation_exhausted incidents expose blocked automatic recovery by source; cumulative request counters do not prove exhaustion. [[src/proxy2vpn/agent/metrics.py#collect_metrics]] supplies the gauge.

## Deployment

An isolated local stack provisions synthetic mixed-source evidence, the real read-only exporter, Prometheus, Alertmanager and Grafana without mounting production state or Docker sockets.

Monitoring HTTP ports bind to host loopback; Grafana requires login. Alerts group deployment symptoms and inhibit downstream noise during collection failures. Explicit targets anchor missing-series checks; individual discovery removals require a separate expectation inventory. See [[metrics#Watchdog Metrics]] and [[monitoring-tests#Monitoring Tests]].
