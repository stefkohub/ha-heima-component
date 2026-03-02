from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.heima.runtime.engine import HeimaEngine


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


class _FakeServices:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, dict(data), blocking))

    def async_services(self):
        return {"notify": {}}


def _engine(options: dict, state_values: dict[str, str] | None = None) -> HeimaEngine:
    hass = SimpleNamespace(
        states=_FakeStates(state_values),
        bus=_FakeBus(),
        services=_FakeServices(),
    )
    engine = HeimaEngine(hass=hass, entry=SimpleNamespace(options=options))
    engine._build_default_state()
    return engine


def _base_options_with_person_and_rooms(num_rooms: int, notifications: dict) -> dict:
    rooms = []
    for i in range(num_rooms):
        rooms.append(
            {
                "room_id": f"room{i+1}",
                "occupancy_mode": "derived",
                "sources": [f"binary_sensor.room{i+1}_presence"],
                "logic": "any_of",
            }
        )
    return {
        "people_named": [{"slug": "stefano", "presence_method": "manual", "enable_override": True}],
        "rooms": rooms,
        "notifications": notifications,
    }


async def _eval(engine: HeimaEngine):
    engine._compute_snapshot(reason="test")
    await engine._emit_queued_events()


def _event_types(engine: HeimaEngine) -> list[str]:
    return [payload["type"] for event_type, payload in engine._hass.bus.events if event_type == "heima_event"]


@pytest.mark.asyncio
async def test_occupancy_mismatch_smart_suppresses_when_coverage_too_low(monkeypatch):
    engine = _engine(
        _base_options_with_person_and_rooms(
            1,
            {
                "enabled_event_categories": ["occupancy", "people"],
                "occupancy_mismatch_policy": "smart",
                "occupancy_mismatch_min_derived_rooms": 2,
                "occupancy_mismatch_persist_s": 0,
            },
        ),
        {"binary_sensor.room1_presence": "off"},
    )
    engine.state.set_select("heima_person_stefano_override", "force_home")
    monkeypatch.setattr("custom_components.heima.runtime.engine.time.monotonic", lambda: 100.0)

    await _eval(engine)

    assert "occupancy.inconsistency_home_no_room" not in _event_types(engine)


@pytest.mark.asyncio
async def test_occupancy_mismatch_smart_requires_persistence(monkeypatch):
    current_t = 100.0
    monkeypatch.setattr("custom_components.heima.runtime.engine.time.monotonic", lambda: current_t)
    engine = _engine(
        _base_options_with_person_and_rooms(
            2,
            {
                "enabled_event_categories": ["occupancy", "people"],
                "occupancy_mismatch_policy": "smart",
                "occupancy_mismatch_min_derived_rooms": 2,
                "occupancy_mismatch_persist_s": 600,
            },
        ),
        {
            "binary_sensor.room1_presence": "off",
            "binary_sensor.room2_presence": "off",
        },
    )
    engine.state.set_select("heima_person_stefano_override", "force_home")

    await _eval(engine)
    assert "occupancy.inconsistency_home_no_room" not in _event_types(engine)
    delay = engine.next_dwell_recheck_delay_s()
    assert delay is not None and delay > 0

    current_t = 750.0
    await _eval(engine)
    assert "occupancy.inconsistency_home_no_room" in _event_types(engine)


@pytest.mark.asyncio
async def test_occupancy_mismatch_strict_emits_immediately(monkeypatch):
    monkeypatch.setattr("custom_components.heima.runtime.engine.time.monotonic", lambda: 100.0)
    engine = _engine(
        _base_options_with_person_and_rooms(
            0,
            {
                "enabled_event_categories": ["occupancy", "people"],
                "occupancy_mismatch_policy": "strict",
                "occupancy_mismatch_min_derived_rooms": 99,
                "occupancy_mismatch_persist_s": 999,
            },
        ),
    )
    engine.state.set_select("heima_person_stefano_override", "force_home")

    await _eval(engine)

    assert "occupancy.inconsistency_home_no_room" in _event_types(engine)
