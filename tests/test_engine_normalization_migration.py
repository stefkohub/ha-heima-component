from __future__ import annotations

from types import SimpleNamespace

from custom_components.heima.runtime.engine import HeimaEngine
from custom_components.heima.runtime.normalization.contracts import build_observation


class _FakeStates:
    def get(self, entity_id: str):
        return None


class _FakeServices:
    def async_services(self):
        return {"notify": {}}

    async def async_call(self, domain, service, data, blocking=False):
        return None


class _FakeBus:
    def async_fire(self, event_type, data):
        return None


class _FakeNormalizer:
    def __init__(self):
        self.presence_calls: list[str | None] = []
        self.boolean_calls: list[str | None] = []

    def presence(self, entity_id: str | None):
        self.presence_calls.append(entity_id)
        return build_observation(
            kind="presence",
            state="on" if entity_id == "binary_sensor.room" else "off",
            confidence=100,
            raw_state="on" if entity_id == "binary_sensor.room" else "off",
            source_entity_id=entity_id,
            reason="fake",
        )

    def boolean_signal(self, entity_id: str | None):
        self.boolean_calls.append(entity_id)
        return build_observation(
            kind="boolean_signal",
            state="on" if entity_id == "binary_sensor.relax_mode" else "off",
            confidence=100,
            raw_state="on" if entity_id == "binary_sensor.relax_mode" else "off",
            source_entity_id=entity_id,
            reason="fake",
        )


def _engine() -> HeimaEngine:
    hass = SimpleNamespace(states=_FakeStates(), services=_FakeServices(), bus=_FakeBus())
    return HeimaEngine(hass=hass, entry=SimpleNamespace(options={}))


def test_is_presence_on_uses_input_normalizer_facade():
    engine = _engine()
    fake = _FakeNormalizer()
    engine._normalizer = fake  # internal swap for migration regression test

    assert engine._is_presence_on("binary_sensor.room") is True
    assert engine._is_presence_on("binary_sensor.other") is False
    assert fake.presence_calls == ["binary_sensor.room", "binary_sensor.other"]


def test_is_on_any_uses_boolean_signal_normalizer_facade():
    engine = _engine()
    fake = _FakeNormalizer()
    engine._normalizer = fake

    result = engine._is_on_any(["binary_sensor.work_window", "binary_sensor.relax_mode"])

    assert result is True
    assert fake.boolean_calls == ["binary_sensor.work_window", "binary_sensor.relax_mode"]

