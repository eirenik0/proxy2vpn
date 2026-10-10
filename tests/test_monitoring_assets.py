"""Operator assets connect metric contracts, dashboards and alert runbooks."""

import json
from pathlib import Path

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]


# @lat: [[lat.md/monitoring-tests#Monitoring Tests#Runbook And Dashboard Contract]]
def test_alert_runbooks_and_dashboard_queries_match_documented_contract():
    assets = ROOT / "monitoring"
    rules = YAML(typ="safe").load((assets / "rules.yml").read_text())
    runbook = (assets / "RUNBOOK.md").read_text()
    headings = {
        line[3:].lower().replace(" ", "-")
        for line in runbook.splitlines()
        if line.startswith("## ")
    }
    alerts = [r for r in rules["groups"][0]["rules"] if "alert" in r]
    assert len(alerts) >= 10
    for rule in alerts:
        assert rule["annotations"]["runbook_url"].split("#")[-1] in headings
        assert rule["for"] != "0m"
        assert set(rule["labels"]) == {"severity"}
    dashboard = json.loads((assets / "grafana/dashboards/fleet.json").read_text())
    assert dashboard["uid"] == "proxy2vpn-fleet"
    assert {v["name"] for v in dashboard["templating"]["list"]} == {
        "source",
        "deployment",
    }
    for panel in dashboard["panels"]:
        if panel["type"] in {"stat", "table"}:
            assert all(target.get("instant") is True for target in panel["targets"])
        assert panel["fieldConfig"]["defaults"]["noValue"] == "No monitoring data"
    expressions = " ".join(
        target["expr"] for panel in dashboard["panels"] for target in panel["targets"]
    )
    for required in (
        "up{",
        "observation_known",
        "observation_fresh",
        "reported_latency_seconds",
        "open_incidents",
        "recovery_action_outcomes_total",
    ):
        assert required in expressions


# @lat: [[lat.md/monitoring-tests#Monitoring Tests#Fresh Checkout Demo]]
def test_documented_compose_is_present_and_isolates_demo_evidence():
    compose = YAML(typ="safe").load((ROOT / "monitoring/compose.yml").read_text())
    services = compose["services"]
    assert set(services) == {
        "producer",
        "exporter",
        "prometheus",
        "alertmanager",
        "grafana",
    }
    assert services["producer"]["volumes"] == ["demo-evidence:/evidence"]
    assert services["exporter"]["volumes"] == ["demo-evidence:/evidence:ro"]
    assert "ports" not in services["exporter"]
    for service in services.values():
        assert all(port.startswith("127.0.0.1:") for port in service.get("ports", []))
        assert all("docker.sock" not in mount for mount in service.get("volumes", []))
    assert services["grafana"]["environment"]["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
