from proxy2vpn.adapters import fleet_state_manager as fleet_state_manager_mod
from proxy2vpn.adapters.compose_manager import ComposeManager


def test_fleet_state_manager_reinitializes_for_new_compose_path(monkeypatch, tmp_path):
    monkeypatch.setattr(fleet_state_manager_mod.FleetStateManager, "_instance", None)
    monkeypatch.setattr(
        fleet_state_manager_mod.FleetStateManager,
        "_instance_compose_path",
        None,
    )

    compose_a = tmp_path / "a.yml"
    compose_b = tmp_path / "b.yml"
    ComposeManager.create_initial_compose(compose_a, force=True)
    ComposeManager.create_initial_compose(compose_b, force=True)

    first = fleet_state_manager_mod.FleetStateManager(str(compose_a))
    second = fleet_state_manager_mod.FleetStateManager(str(compose_b))

    assert first is not second
    assert first.compose_path == compose_a.resolve()
    assert second.compose_path == compose_b.resolve()
