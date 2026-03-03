"""Shared strategy configuration helpers for normalization-driven fusion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


WEIGHTED_QUORUM_STRATEGIES = {"weighted_quorum"}
SIGNAL_SET_PLUGIN_IDS = {
    "any_of": "builtin.any_of",
    "all_of": "builtin.all_of",
    "quorum": "builtin.quorum",
    "weighted_quorum": "builtin.weighted_quorum",
}


@dataclass(frozen=True)
class SignalSetStrategyContract:
    """Reusable contract for a domain-specific signal-set strategy family."""

    allowed_strategies: tuple[str, ...]
    default_strategy: str
    default_fallback_state: str = "off"


GROUP_PRESENCE_STRATEGY_CONTRACT = SignalSetStrategyContract(
    allowed_strategies=("quorum", "weighted_quorum"),
    default_strategy="quorum",
    default_fallback_state="off",
)

ROOM_OCCUPANCY_STRATEGY_CONTRACT = SignalSetStrategyContract(
    allowed_strategies=("any_of", "all_of", "weighted_quorum"),
    default_strategy="any_of",
    default_fallback_state="off",
)

SECURITY_CORROBORATION_STRATEGY_CONTRACT = SignalSetStrategyContract(
    allowed_strategies=("any_of", "all_of"),
    default_strategy="any_of",
    default_fallback_state="off",
)

HOUSE_SIGNAL_STRATEGY_CONTRACT = SignalSetStrategyContract(
    allowed_strategies=("any_of", "all_of"),
    default_strategy="any_of",
    default_fallback_state="off",
)


def normalize_signal_set_strategy_fields(
    data: dict[str, Any],
    *,
    strategy_key: str,
    contract: SignalSetStrategyContract,
) -> dict[str, Any]:
    """Normalize strategy fields using a reusable strategy contract."""
    return normalize_weighted_fusion_fields(
        data,
        strategy_key=strategy_key,
        allowed_strategies=contract.allowed_strategies,
        default_strategy=contract.default_strategy,
    )


def validate_signal_set_strategy_fields(
    *,
    payload: dict[str, Any],
    strategy_key: str,
    sources: list[str],
    contract: SignalSetStrategyContract,
) -> dict[str, str]:
    """Validate strategy fields using a reusable strategy contract."""
    strategy = str(payload.get(strategy_key, contract.default_strategy) or contract.default_strategy)
    if strategy not in contract.allowed_strategies:
        payload = dict(payload)
        payload[strategy_key] = contract.default_strategy
    return validate_weighted_fusion_fields(
        payload=payload,
        strategy_key=strategy_key,
        sources=sources,
    )


def normalize_weighted_fusion_fields(
    data: dict[str, Any],
    *,
    strategy_key: str,
    allowed_strategies: list[str] | tuple[str, ...],
    default_strategy: str,
) -> dict[str, Any]:
    """Normalize shared weighted-fusion fields in-place and return the payload."""
    strategy = str(data.get(strategy_key, default_strategy) or default_strategy).strip()
    if strategy not in allowed_strategies:
        strategy = default_strategy
    data[strategy_key] = strategy

    if strategy not in WEIGHTED_QUORUM_STRATEGIES:
        data.pop("weight_threshold", None)
        data.pop("source_weights", None)
        return data

    if data.get("weight_threshold") in ("", None):
        data.pop("weight_threshold", None)
    elif "weight_threshold" in data:
        data["weight_threshold"] = float(data["weight_threshold"])

    source_weights = normalize_source_weights(data.get("source_weights"))
    if source_weights:
        data["source_weights"] = source_weights
    else:
        data.pop("source_weights", None)
    return data


def normalize_source_weights(value: Any) -> dict[str, float]:
    """Normalize source weights from form input into a stable mapping."""
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        result: dict[str, float] = {}
        for entity_id, weight in value.items():
            entity_key = str(entity_id).strip()
            if not entity_key:
                continue
            try:
                result[entity_key] = float(weight)
            except (TypeError, ValueError):
                continue
        return result

    result: dict[str, float] = {}
    text = str(value)
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        entity_id, raw_weight = line.split("=", 1)
        entity_key = entity_id.strip()
        if not entity_key:
            continue
        try:
            result[entity_key] = float(raw_weight.strip())
        except (TypeError, ValueError):
            continue
    return result


def build_signal_set_strategy_cfg(
    *,
    strategy: str,
    required: int | None = None,
    weight_threshold: Any = None,
    source_weights: Any = None,
    fallback_state: str = "off",
) -> dict[str, Any]:
    """Build shared runtime strategy config for on/off/unknown signal-set fusion."""
    normalized_strategy = str(strategy or "quorum")
    plugin_id = SIGNAL_SET_PLUGIN_IDS.get(normalized_strategy, SIGNAL_SET_PLUGIN_IDS["quorum"])
    cfg: dict[str, Any] = {
        "plugin_id": plugin_id,
        "fallback_state": str(fallback_state or "off"),
    }
    if plugin_id == "builtin.quorum" and required is not None:
        cfg["required"] = int(required)
    elif plugin_id == "builtin.weighted_quorum" and weight_threshold not in (None, ""):
        cfg["threshold"] = float(weight_threshold)
    if plugin_id == "builtin.weighted_quorum" and isinstance(source_weights, dict):
        cfg["weights"] = {
            str(entity_id): float(weight)
            for entity_id, weight in source_weights.items()
            if str(entity_id)
        }
    return cfg


def build_signal_set_strategy_cfg_for_contract(
    *,
    contract: SignalSetStrategyContract,
    strategy: str | None = None,
    required: int | None = None,
    weight_threshold: Any = None,
    source_weights: Any = None,
    fallback_state: str | None = None,
) -> dict[str, Any]:
    """Build strategy config constrained by a reusable domain contract."""
    effective_strategy = str(strategy or contract.default_strategy)
    if effective_strategy not in contract.allowed_strategies:
        effective_strategy = contract.default_strategy
    return build_signal_set_strategy_cfg(
        strategy=effective_strategy,
        required=required,
        weight_threshold=weight_threshold,
        source_weights=source_weights,
        fallback_state=fallback_state or contract.default_fallback_state,
    )


def validate_weighted_fusion_fields(
    *,
    payload: dict[str, Any],
    strategy_key: str,
    sources: list[str],
) -> dict[str, str]:
    """Validate shared weighted-fusion fields for a payload."""
    errors: dict[str, str] = {}
    strategy = str(payload.get(strategy_key, "") or "")
    if strategy not in WEIGHTED_QUORUM_STRATEGIES:
        return errors

    if "weight_threshold" in payload:
        try:
            threshold = float(payload.get("weight_threshold"))
        except (TypeError, ValueError):
            errors["weight_threshold"] = "invalid_number"
        else:
            if threshold <= 0:
                errors["weight_threshold"] = "invalid_number"

    source_weights = payload.get("source_weights", {})
    if not isinstance(source_weights, dict):
        errors["source_weights"] = "invalid_format"
        return errors

    source_ids = {str(source) for source in sources}
    for entity_id, weight in source_weights.items():
        if str(entity_id) not in source_ids:
            errors["source_weights"] = "invalid_mapping"
            return errors
        try:
            if float(weight) <= 0:
                errors["source_weights"] = "invalid_mapping"
                return errors
        except (TypeError, ValueError):
            errors["source_weights"] = "invalid_mapping"
            return errors
    return errors
