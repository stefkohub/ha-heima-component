from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.heima.const import DOMAIN, SERVICE_COMMAND, SERVICE_SET_MODE
from custom_components.heima.runtime.engine import HeimaEngine
from custom_components.heima.services import async_register_services


class _FakeStateObj:
    def __init__(self, state: str):
        self.state = state


class _FakeStates:
    def __init__(self):
        self._values: dict[str, str] = {}

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


class _FakeServicesRegistry:
    def __init__(self):
        self._handlers: dict[tuple[str, str], object] = {}
        self.calls: list[tuple[str, str, dict, bool]] = []

    def async_register(self, domain, service, handler, schema=None):
        self._handlers[(domain, service)] = handler

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, dict(data), blocking))
        return None

    def async_services(self):
        return {
            "notify": {
                "mobile_app_test": object(),
                "mobile_app_alias_test": object(),
            }
        }

    def handler(self, domain, service):
        return self._handlers[(domain, service)]


class _FakeCoordinator:
    def __init__(self, engine, entry_id="entry1"):
        self.engine = engine
        self.entry = SimpleNamespace(entry_id=entry_id)

    async def async_emit_event(self, **kwargs):
        return await self.engine.async_emit_external_event(
            event_type=kwargs["event_type"],
            key=kwargs["key"],
            severity=kwargs["severity"],
            title=kwargs["title"],
            message=kwargs["message"],
            context=kwargs.get("context") or {},
        )

    async def async_request_evaluation(self, reason: str):
        return None

    async def async_set_house_state_override(self, *, mode: str, enabled: bool):
        action, previous, current = self.engine.set_house_state_override(
            mode=mode,
            enabled=enabled,
            source="service:heima.set_mode",
        )
        await self.engine.async_emit_external_event(
            event_type="system.house_state_override_changed",
            key=(
                "system.house_state_override_changed:"
                f"{previous or 'none'}->{current or 'none'}:{action}"
            ),
            severity="info",
            title="House-state override changed",
            message=f"House-state override {action}: {previous or 'none'} -> {current or 'none'}.",
            context={
                "previous": previous,
                "current": current,
                "source": "service:heima.set_mode",
                "action": action,
            },
        )
        await self.engine.async_evaluate(reason=f"service:set_mode:{mode}:{enabled}")
        return action


@pytest.mark.asyncio
async def test_heima_command_notify_event_uses_pipeline_and_updates_sensors(monkeypatch):
    services = _FakeServicesRegistry()
    hass = SimpleNamespace(
        data={DOMAIN: {}},
        services=services,
        bus=_FakeBus(),
        states=_FakeStates(),
    )

    entry = SimpleNamespace(
        options={
            "notifications": {
                "routes": ["mobile_app_test"],
                "recipients": {"stefano": ["mobile_app_alias_test"]},
                "recipient_groups": {"family": ["stefano"]},
                "route_targets": ["family"],
                "dedup_window_s": 60,
                "rate_limit_per_key_s": 300,
            }
        }
    )
    engine = HeimaEngine(hass=hass, entry=entry)
    engine._build_default_state()
    coordinator = _FakeCoordinator(engine)

    await async_register_services(hass)
    monkeypatch.setattr(
        "custom_components.heima.services._coordinators_for_target",
        lambda _hass, _target: [coordinator],
    )

    handler = services.handler(DOMAIN, SERVICE_COMMAND)
    await handler(
        SimpleNamespace(
            data={
                "command": "notify_event",
                "target": {},
                "params": {
                    "type": "debug.manual_test",
                    "key": "debug.manual_test",
                    "severity": "info",
                    "title": "Test",
                    "message": "hello",
                    "context": {"source": "test"},
                },
            }
        )
    )

    # Event bus fired through pipeline
    assert hass.bus.events
    assert hass.bus.events[-1][0] == "heima_event"
    assert hass.bus.events[-1][1]["type"] == "debug.manual_test"

    # Routed to notify.*
    notify_calls = [c for c in services.calls if c[0] == "notify"]
    called_services = [service for _domain, service, _data, _blocking in notify_calls]
    assert called_services == ["mobile_app_test", "mobile_app_alias_test"]
    _, _, notify_payload, _ = notify_calls[-1]
    assert notify_payload["title"] == "Test"
    assert notify_payload["message"] == "hello"
    assert notify_payload["data"]["heima_event_type"] == "debug.manual_test"

    # Canonical sensors updated by the same pipeline
    assert engine.state.get_sensor("heima_last_event") == "debug.manual_test"
    stats_state = engine.state.get_sensor("heima_event_stats")
    assert isinstance(stats_state, str)
    assert "emitted=1" in stats_state
    attrs = engine.state.get_sensor_attributes("heima_event_stats") or {}
    assert attrs.get("last_event", {}).get("type") == "debug.manual_test"


@pytest.mark.asyncio
async def test_heima_set_mode_sets_and_clears_final_house_state_override(monkeypatch):
    services = _FakeServicesRegistry()
    hass = SimpleNamespace(
        data={DOMAIN: {}},
        services=services,
        bus=_FakeBus(),
        states=_FakeStates(),
    )

    entry = SimpleNamespace(options={})
    engine = HeimaEngine(hass=hass, entry=entry)
    engine._build_default_state()
    coordinator = _FakeCoordinator(engine)

    await async_register_services(hass)
    monkeypatch.setattr(
        "custom_components.heima.services._iter_coordinators",
        lambda _hass: [coordinator],
    )

    handler = services.handler(DOMAIN, SERVICE_SET_MODE)
    await handler(SimpleNamespace(data={"mode": "vacation", "state": True}))

    assert engine.snapshot.house_state == "vacation"
    assert engine.state.get_sensor("heima_house_state") == "vacation"
    assert engine.state.get_sensor("heima_house_state_reason") == "manual_override:vacation"
    assert engine.diagnostics()["house_state_override"]["house_state_override"] == "vacation"
    assert hass.bus.events[-1][1]["type"] == "system.house_state_override_changed"
    assert hass.bus.events[-1][1]["context"]["action"] == "set"

    await handler(SimpleNamespace(data={"mode": "vacation", "state": False}))

    assert engine.snapshot.house_state == "away"
    assert engine.state.get_sensor("heima_house_state") == "away"
    assert engine.diagnostics()["house_state_override"]["house_state_override"] is None
    override_events = [
        payload
        for event_type, payload in hass.bus.events
        if event_type == "heima_event" and payload["type"] == "system.house_state_override_changed"
    ]
    assert override_events[-1]["context"]["action"] == "clear"
