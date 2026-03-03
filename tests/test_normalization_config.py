from __future__ import annotations

from custom_components.heima.runtime.normalization.config import (
    build_signal_set_strategy_cfg,
    normalize_source_weights,
    normalize_weighted_fusion_fields,
    validate_weighted_fusion_fields,
)


def test_normalize_source_weights_parses_text_mapping():
    assert normalize_source_weights("binary_sensor.a=0.8\nbinary_sensor.b=0.4") == {
        "binary_sensor.a": 0.8,
        "binary_sensor.b": 0.4,
    }


def test_normalize_weighted_fusion_fields_drops_weighted_fields_for_plain_strategy():
    payload = {
        "group_strategy": "quorum",
        "weight_threshold": "1.2",
        "source_weights": "binary_sensor.a=0.8",
    }

    normalized = normalize_weighted_fusion_fields(
        payload,
        strategy_key="group_strategy",
        allowed_strategies=["quorum", "weighted_quorum"],
        default_strategy="quorum",
    )

    assert normalized["group_strategy"] == "quorum"
    assert "weight_threshold" not in normalized
    assert "source_weights" not in normalized


def test_validate_weighted_fusion_fields_rejects_unknown_weight_sources():
    errors = validate_weighted_fusion_fields(
        payload={
            "group_strategy": "weighted_quorum",
            "weight_threshold": 1.0,
            "source_weights": {"binary_sensor.other": 1.0},
        },
        strategy_key="group_strategy",
        sources=["binary_sensor.a"],
    )

    assert errors == {"source_weights": "invalid_mapping"}


def test_build_signal_set_strategy_cfg_for_weighted_quorum():
    cfg = build_signal_set_strategy_cfg(
        strategy="weighted_quorum",
        required=1,
        weight_threshold=1.2,
        source_weights={
            "binary_sensor.a": 0.8,
            "binary_sensor.b": 0.4,
        },
        fallback_state="off",
    )

    assert cfg == {
        "plugin_id": "builtin.weighted_quorum",
        "fallback_state": "off",
        "threshold": 1.2,
        "weights": {
            "binary_sensor.a": 0.8,
            "binary_sensor.b": 0.4,
        },
    }


def test_build_signal_set_strategy_cfg_for_all_of():
    cfg = build_signal_set_strategy_cfg(
        strategy="all_of",
        fallback_state="off",
    )

    assert cfg == {
        "plugin_id": "builtin.all_of",
        "fallback_state": "off",
    }
