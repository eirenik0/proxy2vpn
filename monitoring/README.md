# Local monitoring example

This isolated demonstration provisions a mixed synthetic fleet, the real read-only exporter, Prometheus rules, Alertmanager grouping and a Grafana dashboard. It requires Docker Compose and touches only its own named volume/network; it has no VPN credentials, Docker socket or production state mounts.

```sh
export GRAFANA_ADMIN_PASSWORD='choose-a-local-password'
docker compose -f monitoring/compose.yml up -d --build
```

Open Grafana at http://127.0.0.1:13000 (admin and your chosen password), Prometheus at http://127.0.0.1:19090 and Alertmanager at http://127.0.0.1:19093. The dashboard is provisioned in the Proxy2VPN folder. Synthetic observations update every 15 seconds; labels identify Gluetun and external-proxy sources without actual VPN connections. Enable sustained simulated failure with `DEMO_CONNECTIVITY_FAILURE=1 docker compose -f monitoring/compose.yml up -d producer`; restore with 0. Stop the producer to demonstrate watchdog/endpoint staleness, and stop the exporter to demonstrate scrape failure. Cleanup only this demonstration:

```sh
docker compose -f monitoring/compose.yml down -v
```

For a real deployment, run `agent metrics` independently beside the local watchdog and replace the sample scrape target. A Prometheus container's localhost is not the host's localhost. Use an authenticated TLS proxy reachable on a restricted private network or an explicit exporter private-address bind and firewall. Do not expose the sample unauthenticated services publicly or mount a production fleet into the demo producer. Keep Grafana login enabled. The sample's fixed 172.29.143.0/24 network may be changed if it conflicts with local routes; change the exporter bind/static IP together.

Dashboards display missing series as “No monitoring data,” expose known/fresh/never-observed counts and separate known availability from its denominator. Source/deployment filters support mixed or external-only deployments; empty source panels are absence, not health. Request outcome graphs show audit counter deltas; first appearances need a baseline and counter resets do not imply negative work.

Validate the real rule engine, including held failure across active-cycle gaps:

```sh
docker run --rm --entrypoint /bin/promtool -v "$PWD/monitoring:/work:ro" -w /work prom/prometheus:v3.5.0 test rules rules.test.yml
```

See [runbooks](RUNBOOK.md) for each alert and tuning. Prometheus [rule tests](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/) specify the fixture format; Grafana [provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/) describes automatic dashboard/datasource loading.
