from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.heima.runtime.normalization import InputNormalizer, NormalizationFusionRegistry
from custom_components.heima.runtime.normalization.builtins import register_builtin_fusion_plugins
from custom_components.heima.runtime.normalization.contracts import DerivedObservation, build_observation


class _FakeStateObj:
    def __init__(self, state: str):
        self.state = state


class _FakeStates:
    def __init__(self, values: dict[str, str] | None = None):
        self._values = dict(values or {})

    def get(self, entity_id: str):
        if entity_id not in self._values:
            return None
        return _FakeStateObj(self._values[entity_id])


def _hass(values: dict[str, str] | None = None):
    return SimpleNamespace(states=_FakeStates(values or {}))


def test_input_normalizer_presence_behaviour_preserving_truthy_and_numeric():
    normalizer = InputNormalizer(_hass({"binary_sensor.motion": "on", "sensor.count": "2", "sensor.zero": "0"}))
    assert normalizer.presence("binary_sensor.motion").state == "on"
    assert normalizer.presence("sensor.count").state == "on"
    off_obs = normalizer.presence("sensor.zero")
    assert off_obs.state == "off"
    assert off_obs.reason == "default_off"


def test_input_normalizer_presence_unknown_unavailable_distinction():
    normalizer = InputNormalizer(
        _hass({"binary_sensor.a": "unknown", "binary_sensor.b": "unavailable"})
    )
    a = normalizer.presence("binary_sensor.a")
    b = normalizer.presence("binary_sensor.b")
    assert a.state == "unknown" and a.available is True
    assert b.state == "unknown" and b.available is False


def test_input_normalizer_security_mapping_and_transition():
    normalizer = InputNormalizer(
        _hass(
            {
                "alarm.home": "armed_away_custom",
                "alarm.home2": "pending",
            }
        )
    )
    obs_away = normalizer.security(
        "alarm.home",
        {"armed_away_value": "armed_away_custom", "armed_home_value": "armed_home_custom"},
    )
    obs_pending = normalizer.security("alarm.home2", {})
    assert obs_away.state == "armed_away"
    assert obs_pending.state == "transition"


def test_builtin_registry_has_core_plugins():
    reg = NormalizationFusionRegistry()
    register_builtin_fusion_plugins(reg)
    ids = {d.plugin_id for d in reg.descriptors()}
    assert {
        "builtin.direct",
        "builtin.any_of",
        "builtin.all_of",
        "builtin.quorum",
        "builtin.weighted_quorum",
    } <= ids


def test_derive_any_of_via_facade_uses_registry_plugin():
    normalizer = InputNormalizer(_hass())
    inputs = [
        build_observation(
            kind="presence",
            state="off",
            confidence=100,
            raw_state="off",
            source_entity_id="binary_sensor.a",
            reason="test",
        ),
        build_observation(
            kind="presence",
            state="on",
            confidence=100,
            raw_state="on",
            source_entity_id="binary_sensor.b",
            reason="test",
        ),
    ]
    result = normalizer.derive(
        kind="presence",
        inputs=inputs,
        strategy_cfg={"plugin_id": "builtin.any_of"},
    )
    assert isinstance(result, DerivedObservation)
    assert result.state == "on"
    assert result.fusion_strategy == "any_of"
    assert result.plugin_id == "builtin.any_of"


def test_registry_rejects_duplicate_plugin_registration():
    reg = NormalizationFusionRegistry()
    register_builtin_fusion_plugins(reg)
    with pytest.raises(ValueError):
        register_builtin_fusion_plugins(reg)


def test_derive_weighted_quorum_via_facade_uses_weights_and_threshold():
    normalizer = InputNormalizer(_hass())
    inputs = [
        build_observation(
            kind="presence",
            state="on",
            confidence=100,
            raw_state="on",
            source_entity_id="binary_sensor.a",
            reason="test",
        ),
        build_observation(
            kind="presence",
            state="off",
            confidence=100,
            raw_state="off",
            source_entity_id="binary_sensor.b",
            reason="test",
        ),
        build_observation(
            kind="presence",
            state="off",
            confidence=100,
            raw_state="off",
            source_entity_id="binary_sensor.c",
            reason="test",
        ),
    ]
    result = normalizer.derive(
        kind="presence",
        inputs=inputs,
        strategy_cfg={
            "plugin_id": "builtin.weighted_quorum",
            "threshold": 0.7,
            "weights": {
                "binary_sensor.a": 0.8,
                "binary_sensor.b": 0.1,
                "binary_sensor.c": 0.1,
            },
        },
    )
    assert isinstance(result, DerivedObservation)
    assert result.state == "on"
    assert result.fusion_strategy == "weighted_quorum"
    assert result.plugin_id == "builtin.weighted_quorum"
    assert result.evidence["on_weight"] == 0.8
    assert result.evidence["threshold"] == 0.7
