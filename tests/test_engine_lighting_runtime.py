from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.exceptions import ServiceNotFound

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
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def async_fire(self, event_type, data):
        self.events.append((event_type, dict(data)))
        return None


class _FakeServices:
    def __init__(self, fail_services: set[tuple[str, str]] | None = None):
        self.calls: list[tuple[str, str, dict, bool]] = []
        self._fail_services = set(fail_services or set())

    async def async_call(self, domain, service, data, blocking=False):
        if (domain, service) in self._fail_services:
            raise ServiceNotFound(domain, service)
        self.calls.append((domain, service, dict(data), blocking))

    def async_services(self):
        return {"notify": {}}


def _entry_with_options(options: dict) -> SimpleNamespace:
    return SimpleNamespace(options=options)


def _build_engine(
    options: dict,
    state_values: dict[str, str] | None = None,
    *,
    fail_services: set[tuple[str, str]] | None = None,
) -> HeimaEngine:
    hass = SimpleNamespace(
        states=_FakeStates(state_values),
        services=_FakeServices(fail_services),
        bus=_FakeBus(),
    )
    engine = HeimaEngine(hass=hass, entry=_entry_with_options(options))
    engine._build_default_state()
    return engine


def test_room_with_occupancy_mode_none_is_off_and_does_not_contribute():
    options = {
        "people_named": [{"slug": "p1", "presence_method": "manual", "enable_override": True}],
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


@pytest.mark.asyncio
async def test_apply_plan_ignores_light_turn_off_service_race():
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
    engine = _build_engine(options, fail_services={("light", "turn_off")})
    snapshot = engine._compute_snapshot(reason="test")
    plan = engine._build_apply_plan(snapshot)

    await engine._execute_apply_plan(plan)

    assert engine._hass.services.calls == []


@pytest.mark.asyncio
async def test_apply_plan_ignores_scene_turn_on_service_race():
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
        "lighting_zones": [{"zone_id": "zona", "rooms": ["soggiorno"]}],
        "lighting_rooms": [
            {
                "room_id": "soggiorno",
                "scene_evening": "scene.soggiorno_evening",
                "enable_manual_hold": True,
            }
        ],
    }
    engine = _build_engine(
        options,
        {"scene.soggiorno_evening": "scening"},
        fail_services={("scene", "turn_on")},
    )
    snapshot = DecisionSnapshot(
        snapshot_id="x",
        ts="2026-01-01T00:00:00+00:00",
        house_state="home",
        anyone_home=True,
        people_count=1,
        occupied_rooms=["soggiorno"],
        lighting_intents={"zona": "scene_evening"},
        security_state="unknown",
        notes="test",
    )
    plan = engine._build_apply_plan(snapshot)
    assert len(plan.steps) == 1

    await engine._execute_apply_plan(plan)

    assert engine._hass.services.calls == []


@pytest.mark.asyncio
async def test_scene_missing_event_includes_expected_scene_context():
    options = {
        "people_named": [{"slug": "p1", "presence_method": "manual", "enable_override": True}],
        "rooms": [
            {
                "room_id": "studio",
                "area_id": None,
                "occupancy_mode": "derived",
                "sources": ["binary_sensor.studio_presence"],
                "logic": "any_of",
            }
        ],
        "lighting_zones": [{"zone_id": "zona", "rooms": ["studio"]}],
        "lighting_rooms": [{"room_id": "studio", "enable_manual_hold": True}],
    }
    engine = _build_engine(options, {"binary_sensor.studio_presence": "on"})
    engine.state.set_select("heima_person_p1_override", "force_home")
    snapshot = engine._compute_snapshot(reason="test")
    _ = engine._build_apply_plan(snapshot)
    await engine._emit_queued_events()

    event_payloads = [payload for event_type, payload in engine._hass.bus.events if event_type == "heima_event"]
    scene_missing = [p for p in event_payloads if p["type"] == "lighting.scene_missing"]
    assert scene_missing, "Expected lighting.scene_missing event"
    assert scene_missing[-1]["context"]["room"] == "studio"
    assert scene_missing[-1]["context"]["intent"] == "scene_evening"
    assert scene_missing[-1]["context"]["expected_scene"] == "scene_evening"


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
        security_state="unknown",
        notes="test",
    )

    plan = engine._build_apply_plan(snapshot)

    assert len(plan.steps) == 1
    assert plan.steps[0].params == {"entity_id": "scene.soggiorno_evening"}
    diagnostics = engine.diagnostics()
    conflicts = diagnostics["lighting"]["conflicts_last_eval"]
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["room_id"] == "soggiorno"
    assert conflict["policy"] == "first_wins"
    assert conflict["winning_zone"] == "zona_a"
    assert conflict["dropped_zone"] == "zona_b"


def test_conflict_first_valid_step_wins_after_prior_skip():
    engine = _build_engine(
        {
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
                    "scene_off": "scene.soggiorno_off",
                    "enable_manual_hold": True,
                }
            ],
        }
    )
    # First zone ('scene_evening') produces no valid step due to missing scene.
    # Second zone ('off') produces the first valid step and therefore wins.
    snapshot = DecisionSnapshot(
        snapshot_id="x",
        ts="2026-01-01T00:00:00+00:00",
        house_state="home",
        anyone_home=True,
        people_count=1,
        occupied_rooms=["soggiorno"],
        lighting_intents={"zona_a": "scene_evening", "zona_b": "off"},
        security_state="unknown",
        notes="test",
    )
    plan = engine._build_apply_plan(snapshot)
    assert len(plan.steps) == 1
    assert plan.steps[0].action == "scene.turn_on"
    assert plan.steps[0].params == {"entity_id": "scene.soggiorno_off"}
    assert engine.diagnostics()["lighting"]["conflicts_last_eval"] == []


@pytest.mark.asyncio
async def test_security_armed_away_but_home_event_emitted():
    options = {
        "people_named": [{"slug": "stefano", "presence_method": "manual", "enable_override": True}],
        "security": {
            "enabled": True,
            "security_state_entity": "alarm_control_panel.home",
            "armed_away_value": "armed_away",
            "armed_home_value": "armed_home",
        },
        "notifications": {
            "enabled_event_categories": ["security", "people"],
            "security_mismatch_policy": "strict",
            "security_mismatch_persist_s": 0,
        },
    }
    engine = _build_engine(options, {"alarm_control_panel.home": "armed_away"})
    engine.state.set_select("heima_person_stefano_override", "force_home")

    _ = engine._compute_snapshot(reason="test")
    await engine._emit_queued_events()

    event_payloads = [payload for event_type, payload in engine._hass.bus.events if event_type == "heima_event"]
    security_events = [p for p in event_payloads if p["type"] == "security.armed_away_but_home"]
    assert security_events, "Expected security.armed_away_but_home event"
    assert security_events[-1]["context"]["security_state"] == "armed_away"
    assert security_events[-1]["context"]["people_home_list"] == ["stefano"]
