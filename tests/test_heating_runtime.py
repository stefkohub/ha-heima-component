from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.heima.runtime.engine import HeimaEngine


class _FakeStateObj:
    def __init__(self, state: str, attributes: dict[str, object] | None = None):
        self.state = state
        self.attributes = dict(attributes or {})


class _FakeStates:
    def __init__(self, values: dict[str, object] | None = None):
        self._values = dict(values or {})

    def get(self, entity_id: str):
        value = self._values.get(entity_id)
        if value is None:
            return None
        if isinstance(value, tuple):
            state, attrs = value
            return _FakeStateObj(str(state), attrs)
        return _FakeStateObj(str(value))


class _FakeBus:
    def async_fire(self, event_type, data):
        return None


class _FakeServices:
    def __init__(self):
        self.calls: list[tuple[str, str, dict, bool]] = []

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, dict(data), blocking))

    def async_services(self):
        return {"notify": {}}


def _entry_with_options(options: dict) -> SimpleNamespace:
    return SimpleNamespace(options=options)


def _build_engine(
    options: dict,
    state_values: dict[str, object] | None = None,
) -> HeimaEngine:
    hass = SimpleNamespace(
        states=_FakeStates(state_values),
        services=_FakeServices(),
        bus=_FakeBus(),
    )
    engine = HeimaEngine(hass=hass, entry=_entry_with_options(options))
    engine._build_default_state()
    return engine


@pytest.mark.asyncio
async def test_fixed_target_branch_builds_and_executes_heating_apply_step():
    options = {
        "heating": {
            "climate_entity": "climate.test_thermostat",
            "apply_mode": "set_temperature",
            "temperature_step": 0.5,
            "manual_override_guard": True,
            "override_branches": {
                "away": {
                    "branch": "fixed_target",
                    "target_temperature": 20.0,
                }
            },
        }
    }
    engine = _build_engine(
        options,
        {
            "climate.test_thermostat": ("heat", {"temperature": 18.0}),
        },
    )

    snapshot = engine._compute_snapshot(reason="test")
    plan = engine._build_apply_plan(snapshot)

    assert engine.state.get_sensor("heima_heating_state") == "target_active"
    assert engine.state.get_sensor("heima_heating_reason") == "fixed_target_branch"
    assert engine.state.get_sensor("heima_heating_phase") == "fixed_target"
    assert engine.state.get_sensor("heima_heating_target_temp") == 20.0
    assert any(step.action == "climate.set_temperature" for step in plan.steps)

    await engine._execute_apply_plan(plan)

    assert engine._hass.services.calls[-1] == (
        "climate",
        "set_temperature",
        {
            "entity_id": "climate.test_thermostat",
            "hvac_mode": "heat",
            "temperature": 20.0,
        },
        False,
    )


def test_fixed_target_branch_skips_small_delta_and_sets_guard():
    options = {
        "heating": {
            "climate_entity": "climate.test_thermostat",
            "apply_mode": "set_temperature",
            "temperature_step": 0.5,
            "manual_override_guard": True,
            "override_branches": {
                "away": {
                    "branch": "fixed_target",
                    "target_temperature": 20.0,
                }
            },
        }
    }
    engine = _build_engine(
        options,
        {
            "climate.test_thermostat": ("heat", {"temperature": 19.8}),
        },
    )

    snapshot = engine._compute_snapshot(reason="test")
    plan = engine._build_apply_plan(snapshot)

    assert snapshot.house_state == "away"
    assert engine.state.get_sensor("heima_heating_state") == "idle"
    assert engine.state.get_sensor("heima_heating_reason") == "small_delta_skip"
    assert engine.state.get_binary("heima_heating_applying_guard") is True
    assert not any(step.action == "climate.set_temperature" for step in plan.steps)


def test_heating_without_active_override_branch_delegates_to_scheduler():
    options = {
        "heating": {
            "climate_entity": "climate.test_thermostat",
            "apply_mode": "delegate_to_scheduler",
            "temperature_step": 0.5,
            "manual_override_guard": True,
            "override_branches": {
                "vacation": {
                    "branch": "fixed_target",
                    "target_temperature": 18.0,
                }
            },
        }
    }
    engine = _build_engine(
        options,
        {
            "climate.test_thermostat": ("heat", {"temperature": 18.0}),
        },
    )

    snapshot = engine._compute_snapshot(reason="test")
    plan = engine._build_apply_plan(snapshot)
    trace = engine.diagnostics()["heating"]

    assert snapshot.house_state == "away"
    assert engine.state.get_sensor("heima_heating_state") == "delegated"
    assert engine.state.get_sensor("heima_heating_reason") == "normal_branch"
    assert engine.state.get_sensor("heima_heating_phase") == "normal"
    assert engine.state.get_sensor("heima_heating_target_temp") is None
    assert not any(step.domain == "heating" for step in plan.steps)
    assert trace["selected_branch"] == "disabled"
    assert trace["apply_allowed"] is False
