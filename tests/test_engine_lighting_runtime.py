from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.heima.runtime.engine import HeimaEngine
from custom_components.heima.runtime.snapshot import DecisionSnapshot


class _FakeStateObj:
    def __init__(self, state: str):
        self.state = state


class _FakeStates:
    def __init__(self, values: dict[str, str] | None = None):
        self._values = dict(values or {})

    def get(self, entity_id: str):
        value = self._values.get(entity_id)
        if value is None:
            return None
        return _FakeStateObj(value)


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


def _build_engine(options: dict, state_values: dict[str, str] | None = None) -> HeimaEngine:
    hass = SimpleNamespace(
        states=_FakeStates(state_values),
        services=_FakeServices(),
        bus=_FakeBus(),
    )
    engine = HeimaEngine(hass=hass, entry=_entry_with_options(options))
    engine._build_default_state()
    return engine


def test_room_with_occupancy_mode_none_is_off_and_does_not_contribute():
    options = {
        "rooms": [
            {
                "room_id": "soggiorno",
                "display_name": "Soggiorno",
                "area_id": "soggiorno",
                "occupancy_mode": "none",
                "sources": [],
                "logic": "any_of",
            }
        ],
        "lighting_zones": [{"zone_id": "zona_giorno", "rooms": ["soggiorno"]}],
        "lighting_rooms": [{"room_id": "soggiorno"}],
        "people_named": [
            {"slug": "p1", "presence_method": "manual", "enable_override": True},
        ],
    }
    engine = _build_engine(options)
    engine.state.set_select("heima_person_p1_override", "force_home")

    snapshot = engine._compute_snapshot(reason="test")

    assert snapshot.house_state == "home"
    assert "soggiorno" not in snapshot.occupied_rooms
    assert engine.state.get_binary("heima_occ_soggiorno") is False
    assert engine.state.get_sensor("heima_occ_soggiorno_source") == "none"


def test_zone_auto_with_only_non_sensorized_rooms_resolves_off():
    options = {
        "rooms": [
            {
                "room_id": "soggiorno",
                "area_id": "soggiorno",
                "occupancy_mode": "none",
                "sources": [],
                "logic": "any_of",
            }
        ],
        "lighting_zones": [{"zone_id": "lavoro", "rooms": ["soggiorno"]}],
        "lighting_rooms": [{"room_id": "soggiorno"}],
        "people_named": [
            {"slug": "p1", "presence_method": "manual", "enable_override": True},
        ],
    }
    engine = _build_engine(options)
    engine.state.set_select("heima_person_p1_override", "force_home")

    snapshot = engine._compute_snapshot(reason="test")

    assert snapshot.house_state == "home"
    assert snapshot.lighting_intents["lavoro"] == "off"
    zone_trace = engine.diagnostics()["lighting"]["zone_trace"]["lavoro"]
    assert zone_trace["occupancy_capable_rooms"] == []
    assert zone_trace["zone_occupied"] is False


@pytest.mark.asyncio
async def test_off_without_scene_uses_area_light_turn_off_fallback():
    options = {
        "rooms": [
            {
                "room_id": "soggiorno",
                "area_id": "soggiorno",
                "occupancy_mode": "none",
                "sources": [],
                "logic": "any_of",
            }
        ],
        "lighting_zones": [{"zone_id": "zona", "rooms": ["soggiorno"]}],
        "lighting_rooms": [{"room_id": "soggiorno", "enable_manual_hold": True}],
    }
    engine = _build_engine(options)

    snapshot = engine._compute_snapshot(reason="test")
    plan = engine._build_apply_plan(snapshot)

    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.action == "light.turn_off"
    assert step.params == {"area_id": "soggiorno"}

    await engine._execute_apply_plan(plan)
    assert engine._hass.services.calls[-1] == (
        "light",
        "turn_off",
        {"area_id": "soggiorno"},
        False,
    )


def test_room_in_multiple_zones_reports_conflict_in_diagnostics():
    options = {
        "rooms": [
            {
                "room_id": "soggiorno",
                "area_id": "soggiorno",
                "occupancy_mode": "derived",
                "sources": ["binary_sensor.soggiorno_presence"],
                "logic": "any_of",
            }
        ],
        "lighting_zones": [
            {"zone_id": "zona_a", "rooms": ["soggiorno"]},
            {"zone_id": "zona_b", "rooms": ["soggiorno"]},
        ],
        "lighting_rooms": [
            {
                "room_id": "soggiorno",
                "scene_evening": "scene.soggiorno_evening",
                "enable_manual_hold": True,
            }
        ],
    }
    engine = _build_engine(options)
    snapshot = DecisionSnapshot(
        snapshot_id="x",
        ts="2026-01-01T00:00:00+00:00",
        house_state="home",
        anyone_home=True,
        people_count=1,
        occupied_rooms=["soggiorno"],
        lighting_intents={"zona_a": "scene_evening", "zona_b": "scene_evening"},
        heating_intent="auto",
        security_state="unknown",
        notes="test",
    )

    plan = engine._build_apply_plan(snapshot)

    assert len(plan.steps) == 2
    diagnostics = engine.diagnostics()
    conflicts = diagnostics["lighting"]["conflicts_last_eval"]
    assert len(conflicts) == 1
    assert conflicts[0]["room_id"] == "soggiorno"
