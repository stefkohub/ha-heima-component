from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.heima.runtime.engine import HeimaEngine
from custom_components.heima.runtime.normalization import InputNormalizer, NormalizationFusionRegistry


class _ExplodingAnyOfPlugin:
    plugin_id = "builtin.any_of"
    plugin_api_version = 1
    supported_kinds = ("presence",)

    def derive(self, *, kind, inputs, strategy_cfg=None, context=None):
        raise RuntimeError("boom")


class _FakeStateObj:
    def __init__(self, state: str):
        self.state = state


class _MutableStates:
    def __init__(self, values: dict[str, str] | None = None):
        self._values = dict(values or {})

    def get(self, entity_id: str):
        value = self._values.get(entity_id)
        if value is None:
            return None
        return _FakeStateObj(value)

    def set(self, entity_id: str, value: str) -> None:
        self._values[entity_id] = value


class _FakeBus:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def async_fire(self, event_type, data):
        self.events.append((event_type, dict(data)))
        return None


class _FakeServices:
    def __init__(self):
        self.calls: list[tuple[str, str, dict, bool]] = []

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, dict(data), blocking))

    def async_services(self):
        return {"notify": {}}


def _engine(states: _MutableStates, options: dict) -> HeimaEngine:
    hass = SimpleNamespace(states=states, services=_FakeServices(), bus=_FakeBus())
    engine = HeimaEngine(hass=hass, entry=SimpleNamespace(options=options))
    engine._build_default_state()
    return engine


@pytest.mark.asyncio
async def test_room_on_dwell_delays_transition_from_off_to_on(monkeypatch):
    t = 0.0
    monkeypatch.setattr("custom_components.heima.runtime.engine.time.monotonic", lambda: t)
    states = _MutableStates({"binary_sensor.room_presence": "off"})
    engine = _engine(
        states,
        {
            "rooms": [
                {
                    "room_id": "room",
                    "occupancy_mode": "derived",
                    "sources": ["binary_sensor.room_presence"],
                    "logic": "any_of",
                    "on_dwell_s": 10,
                    "off_dwell_s": 0,
                }
            ]
        },
    )

    snap = engine._compute_snapshot(reason="t0")
    assert "room" not in snap.occupied_rooms

    t = 1.0
    states.set("binary_sensor.room_presence", "on")
    snap = engine._compute_snapshot(reason="t1")
    assert "room" not in snap.occupied_rooms
    delay = engine.next_dwell_recheck_delay_s()
    assert delay is not None and delay > 0

    t = 12.0
    snap = engine._compute_snapshot(reason="t12")
    assert "room" in snap.occupied_rooms


@pytest.mark.asyncio
async def test_room_off_dwell_delays_transition_from_on_to_off(monkeypatch):
    t = 0.0
    monkeypatch.setattr("custom_components.heima.runtime.engine.time.monotonic", lambda: t)
    states = _MutableStates({"binary_sensor.room_presence": "on"})
    engine = _engine(
        states,
        {
            "rooms": [
                {
                    "room_id": "room",
                    "occupancy_mode": "derived",
                    "sources": ["binary_sensor.room_presence"],
                    "logic": "any_of",
                    "on_dwell_s": 0,
                    "off_dwell_s": 10,
                }
            ]
        },
    )

    snap = engine._compute_snapshot(reason="t0")
    assert "room" in snap.occupied_rooms

    t = 1.0
    states.set("binary_sensor.room_presence", "off")
    snap = engine._compute_snapshot(reason="t1")
    assert "room" in snap.occupied_rooms

    t = 12.0
    snap = engine._compute_snapshot(reason="t12")
    assert "room" not in snap.occupied_rooms


@pytest.mark.asyncio
async def test_room_max_on_forces_off_and_emits_event(monkeypatch):
    t = 0.0
    monkeypatch.setattr("custom_components.heima.runtime.engine.time.monotonic", lambda: t)
    states = _MutableStates({"binary_sensor.room_presence": "on"})
    engine = _engine(
        states,
        {
            "rooms": [
                {
                    "room_id": "room",
                    "occupancy_mode": "derived",
                    "sources": ["binary_sensor.room_presence"],
                    "logic": "any_of",
                    "on_dwell_s": 0,
                    "off_dwell_s": 0,
                    "max_on_s": 5,
                }
            ]
        },
    )

    snap = engine._compute_snapshot(reason="t0")
    assert "room" in snap.occupied_rooms

    t = 6.0
    snap = engine._compute_snapshot(reason="t6")
    assert "room" not in snap.occupied_rooms
    await engine._emit_queued_events()

    event_types = [p["type"] for e, p in engine._hass.bus.events if e == "heima_event"]
    assert "occupancy.max_on_timeout" in event_types


@pytest.mark.asyncio
async def test_room_weighted_quorum_uses_threshold_for_effective_occupancy(monkeypatch):
    t = 0.0
    monkeypatch.setattr("custom_components.heima.runtime.engine.time.monotonic", lambda: t)
    states = _MutableStates(
        {
            "binary_sensor.room_presence_a": "on",
            "binary_sensor.room_presence_b": "off",
            "binary_sensor.room_presence_c": "off",
        }
    )
    engine = _engine(
        states,
        {
            "rooms": [
                {
                    "room_id": "room",
                    "occupancy_mode": "derived",
                    "sources": [
                        "binary_sensor.room_presence_a",
                        "binary_sensor.room_presence_b",
                        "binary_sensor.room_presence_c",
                    ],
                    "logic": "weighted_quorum",
                    "weight_threshold": 2.0,
                    "on_dwell_s": 0,
                    "off_dwell_s": 0,
                }
            ]
        },
    )

    snap = engine._compute_snapshot(reason="t0")
    assert "room" not in snap.occupied_rooms

    states.set("binary_sensor.room_presence_b", "on")
    snap = engine._compute_snapshot(reason="t1")
    assert "room" in snap.occupied_rooms
    trace = engine.diagnostics()["occupancy"]["room_trace"]["room"]
    assert trace["plugin_id"] == "builtin.weighted_quorum"
    assert trace["fused_observation"]["fusion_strategy"] == "weighted_quorum"
    assert trace["used_plugin_fallback"] is False


@pytest.mark.asyncio
async def test_room_weighted_quorum_uses_configured_source_weights_in_trace(monkeypatch):
    t = 0.0
    monkeypatch.setattr("custom_components.heima.runtime.engine.time.monotonic", lambda: t)
    states = _MutableStates(
        {
            "binary_sensor.room_presence_a": "on",
            "binary_sensor.room_presence_b": "off",
            "binary_sensor.room_presence_c": "off",
        }
    )
    engine = _engine(
        states,
        {
            "rooms": [
                {
                    "room_id": "room",
                    "occupancy_mode": "derived",
                    "sources": [
                        "binary_sensor.room_presence_a",
                        "binary_sensor.room_presence_b",
                        "binary_sensor.room_presence_c",
                    ],
                    "logic": "weighted_quorum",
                    "weight_threshold": 0.7,
                    "source_weights": {
                        "binary_sensor.room_presence_a": 0.8,
                        "binary_sensor.room_presence_b": 0.1,
                        "binary_sensor.room_presence_c": 0.1,
                    },
                    "on_dwell_s": 0,
                    "off_dwell_s": 0,
                }
            ]
        },
    )

    snap = engine._compute_snapshot(reason="t0")
    assert "room" in snap.occupied_rooms

    trace = engine.diagnostics()["occupancy"]["room_trace"]["room"]
    assert trace["configured_source_weights"]["binary_sensor.room_presence_a"] == 0.8
    assert trace["effective_source_weights"]["binary_sensor.room_presence_a"] == 0.8
    contributions = {
        item["entity_id"]: item
        for item in trace["source_weight_contributions"]
    }
    assert contributions["binary_sensor.room_presence_a"]["weight"] == 0.8
    assert contributions["binary_sensor.room_presence_a"]["contributes_to_on"] is True


@pytest.mark.asyncio
async def test_room_occupancy_fusion_failure_uses_fail_safe_off_fallback(monkeypatch):
    t = 0.0
    monkeypatch.setattr("custom_components.heima.runtime.engine.time.monotonic", lambda: t)
    states = _MutableStates({"binary_sensor.room_presence": "on"})
    engine = _engine(
        states,
        {
            "rooms": [
                {
                    "room_id": "room",
                    "occupancy_mode": "derived",
                    "sources": ["binary_sensor.room_presence"],
                    "logic": "any_of",
                    "on_dwell_s": 0,
                    "off_dwell_s": 0,
                }
            ]
        },
    )
    registry = NormalizationFusionRegistry()
    registry.register(_ExplodingAnyOfPlugin())
    engine._normalizer = InputNormalizer(engine._hass, fusion_registry=registry)

    snap = engine._compute_snapshot(reason="t0")

    assert "room" not in snap.occupied_rooms
    trace = engine.diagnostics()["occupancy"]["room_trace"]["room"]
    assert trace["plugin_id"] == "builtin.any_of"
    assert trace["used_plugin_fallback"] is True
    assert trace["fused_observation"]["state"] == "off"
    assert trace["fused_observation"]["reason"] == "plugin_error_fallback"
    assert trace["fused_observation"]["evidence"]["fallback"] == "off"
