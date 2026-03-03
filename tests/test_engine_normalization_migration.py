from __future__ import annotations

from types import SimpleNamespace

from custom_components.heima.runtime.engine import HeimaEngine
from custom_components.heima.runtime.normalization.contracts import build_observation


class _FakeStates:
    def __init__(self, values: dict[str, str] | None = None):
        self._values = dict(values or {})

    def get(self, entity_id: str):
        value = self._values.get(entity_id)
        if value is None:
            return None
        return SimpleNamespace(state=value)


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
        self.derive_calls: list[tuple[str, str, dict]] = []

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

    def derive(self, *, kind, inputs, strategy_cfg=None, context=None):
        cfg = dict(strategy_cfg or {})
        self.derive_calls.append((kind, str(cfg.get("plugin_id")), cfg))
        required = int(cfg.get("required", 1))
        on_count = sum(1 for obs in inputs if obs.state == "on")
        state = "on" if on_count >= required else "off"
        return build_observation(
            kind=kind,
            state=state,
            confidence=100 if state == "on" else 0,
            raw_state=None,
            source_entity_id=None,
            reason="derived",
        )

    def diagnostics(self):
        return {"derive_calls": len(self.derive_calls)}


class _FailSafeCaptureNormalizer(_FakeNormalizer):
    def derive(self, *, kind, inputs, strategy_cfg=None, context=None):
        cfg = dict(strategy_cfg or {})
        self.derive_calls.append((kind, str(cfg.get("plugin_id")), cfg))
        return build_observation(
            kind=kind,
            state="off",
            confidence=0,
            raw_state=None,
            source_entity_id=None,
            reason="derived",
        )


def _engine(state_values: dict[str, str] | None = None) -> HeimaEngine:
    hass = SimpleNamespace(states=_FakeStates(state_values), services=_FakeServices(), bus=_FakeBus())
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


def test_compute_group_presence_uses_quorum_plugin():
    engine = _engine()
    fake = _FakeNormalizer()
    engine._normalizer = fake

    fused, active_count = engine._compute_group_presence(
        ["binary_sensor.room", "binary_sensor.other"], required=1
    )

    assert fused.state == "on"
    assert active_count == 1
    assert fake.derive_calls == [
        (
            "presence",
            "builtin.quorum",
            {
                "plugin_id": "builtin.quorum",
                "required": 1,
                "fallback_state": "off",
            },
        )
    ]


def test_is_entity_home_uses_presence_normalizer():
    engine = _engine()
    fake = _FakeNormalizer()
    engine._normalizer = fake

    assert engine._is_entity_home("binary_sensor.room") is True
    assert engine._is_entity_home("binary_sensor.other") is False


def test_engine_diagnostics_include_normalizer_diagnostics():
    engine = _engine()
    fake = _FakeNormalizer()
    engine._normalizer = fake

    diagnostics = engine.diagnostics()

    assert diagnostics["normalization"] == {"derive_calls": 0}


def test_compute_group_presence_requests_fail_safe_off_fallback():
    engine = _engine()
    fake = _FailSafeCaptureNormalizer()
    engine._normalizer = fake

    fused, active_count = engine._compute_group_presence(
        ["binary_sensor.room", "binary_sensor.other"], required=1
    )

    assert fused.state == "off"
    assert active_count == 1
    assert fake.derive_calls == [
        (
            "presence",
            "builtin.quorum",
            {
                "plugin_id": "builtin.quorum",
                "required": 1,
                "fallback_state": "off",
            },
        )
    ]


def test_compute_group_presence_records_local_trace():
    engine = _engine(
        {
            "binary_sensor.room": "on",
            "binary_sensor.other": "off",
        }
    )

    fused, active_count = engine._compute_group_presence(
        ["binary_sensor.room", "binary_sensor.other"],
        required=1,
        trace_key="person:stefano",
    )

    assert fused.state == "on"
    assert active_count == 1
    diagnostics = engine.diagnostics()
    trace = diagnostics["presence"]["group_trace"]["person:stefano"]
    assert trace["plugin_id"] == "builtin.quorum"
    assert trace["group_strategy"] == "quorum"
    assert trace["required"] == 1
    assert trace["active_count"] == 1
    assert trace["used_plugin_fallback"] is False
    assert trace["fused_observation"]["state"] == "on"


def test_compute_group_presence_supports_weighted_quorum_strategy():
    engine = _engine()
    fake = _FakeNormalizer()
    engine._normalizer = fake

    fused, active_count = engine._compute_group_presence(
        ["binary_sensor.room", "binary_sensor.other"],
        required=1,
        strategy="weighted_quorum",
        weight_threshold=1.2,
        source_weights={
            "binary_sensor.room": 0.8,
            "binary_sensor.other": 0.4,
        },
    )

    assert fused.state == "on"
    assert active_count == 1
    assert fake.derive_calls == [
        (
            "presence",
            "builtin.weighted_quorum",
            {
                "plugin_id": "builtin.weighted_quorum",
                "fallback_state": "off",
                "threshold": 1.2,
                "weights": {
                    "binary_sensor.room": 0.8,
                    "binary_sensor.other": 0.4,
                },
            },
        )
    ]
