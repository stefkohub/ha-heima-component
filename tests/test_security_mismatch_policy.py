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
    def async_services(self):
        return {"notify": {}}

    async def async_call(self, domain, service, data, blocking=False):
        return None


def _engine(options: dict, state_values: dict[str, str] | None = None) -> HeimaEngine:
    hass = SimpleNamespace(states=_FakeStates(state_values), bus=_FakeBus(), services=_FakeServices())
    engine = HeimaEngine(hass=hass, entry=SimpleNamespace(options=options))
    engine._build_default_state()
    return engine


def _event_types(engine: HeimaEngine) -> list[str]:
    return [payload["type"] for event_type, payload in engine._hass.bus.events if event_type == "heima_event"]


async def _eval(engine: HeimaEngine):
    engine._compute_snapshot(reason="test")
    await engine._emit_queued_events()


def _base_options(notifications: dict[str, object]) -> dict[str, object]:
    return {
        "people_named": [{"slug": "stefano", "presence_method": "manual", "enable_override": True}],
        "rooms": [
            {
                "room_id": "soggiorno",
                "occupancy_mode": "derived",
                "sources": ["binary_sensor.soggiorno_presence"],
                "logic": "any_of",
            }
        ],
        "security": {
            "enabled": True,
            "security_state_entity": "alarm_control_panel.home",
            "armed_away_value": "armed_away",
            "armed_home_value": "armed_home",
        },
        "notifications": notifications,
    }


@pytest.mark.asyncio
async def test_security_mismatch_smart_suppresses_without_corroboration(monkeypatch):
    monkeypatch.setattr("custom_components.heima.runtime.engine.time.monotonic", lambda: 100.0)
    engine = _engine(
        _base_options(
            {
                "enabled_event_categories": ["security", "people"],
                "security_mismatch_policy": "smart",
                "security_mismatch_persist_s": 0,
            }
        ),
        {
            "alarm_control_panel.home": "armed_away",
            "binary_sensor.soggiorno_presence": "off",
        },
    )
    engine.state.set_select("heima_person_stefano_override", "force_home")

    await _eval(engine)

    assert "security.armed_away_but_home" not in _event_types(engine)


@pytest.mark.asyncio
async def test_security_mismatch_smart_requires_persistence_with_corroboration(monkeypatch):
    current_t = 100.0
    monkeypatch.setattr("custom_components.heima.runtime.engine.time.monotonic", lambda: current_t)
    engine = _engine(
        _base_options(
            {
                "enabled_event_categories": ["security", "people"],
                "security_mismatch_policy": "smart",
                "security_mismatch_persist_s": 300,
            }
        ),
        {
            "alarm_control_panel.home": "armed_away",
            "binary_sensor.soggiorno_presence": "on",
        },
    )
    engine.state.set_select("heima_person_stefano_override", "force_home")

    await _eval(engine)
    assert "security.armed_away_but_home" not in _event_types(engine)
    delay = engine.next_dwell_recheck_delay_s()
    assert delay is not None and delay > 0

    current_t = 450.0
    await _eval(engine)
    assert "security.armed_away_but_home" in _event_types(engine)


@pytest.mark.asyncio
async def test_security_mismatch_strict_emits_immediately_without_corroboration(monkeypatch):
    monkeypatch.setattr("custom_components.heima.runtime.engine.time.monotonic", lambda: 100.0)
    engine = _engine(
        _base_options(
            {
                "enabled_event_categories": ["security", "people"],
                "security_mismatch_policy": "strict",
                "security_mismatch_persist_s": 999,
            }
        ),
        {
            "alarm_control_panel.home": "armed_away",
            "binary_sensor.soggiorno_presence": "off",
        },
    )
    engine.state.set_select("heima_person_stefano_override", "force_home")

    await _eval(engine)

    assert "security.armed_away_but_home" in _event_types(engine)


@pytest.mark.asyncio
async def test_security_mismatch_uses_normalized_custom_armed_away_mapping(monkeypatch):
    monkeypatch.setattr("custom_components.heima.runtime.engine.time.monotonic", lambda: 100.0)
    options = _base_options(
        {
            "enabled_event_categories": ["security", "people"],
            "security_mismatch_policy": "strict",
            "security_mismatch_persist_s": 999,
        }
    )
    options["security"]["armed_away_value"] = "armed_away_custom"
    options["security"]["armed_home_value"] = "armed_home_custom"
    engine = _engine(
        options,
        {
            "alarm_control_panel.home": "armed_away_custom",
            "binary_sensor.soggiorno_presence": "off",
        },
    )
    engine.state.set_select("heima_person_stefano_override", "force_home")

    await _eval(engine)

    assert "security.armed_away_but_home" in _event_types(engine)
