"""Built-in signal-fusion strategies for the normalization layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import DerivedObservation, NormalizedObservation


def _input_refs(inputs: list[NormalizedObservation]) -> list[str]:
    refs: list[str] = []
    for obs in inputs:
        refs.append(obs.source_entity_id or f"{obs.kind}:{obs.state}")
    return refs


def _mk_derived(
    *,
    kind: str,
    state: str,
    confidence: int,
    inputs: list[NormalizedObservation],
    strategy: str,
    plugin_id: str,
    reason: str,
    evidence: dict[str, Any] | None = None,
    available: bool = True,
    stale: bool = False,
    raw_state: str | None = None,
) -> DerivedObservation:
    return DerivedObservation(
        kind=kind,
        state=state,
        confidence=max(0, min(100, int(confidence))),
        raw_state=raw_state,
        source_entity_id=None,
        stale=stale,
        available=available,
        reason=reason,
        inputs=_input_refs(inputs),
        fusion_strategy=strategy,
        plugin_id=plugin_id,
        plugin_api_version=1,
        evidence=dict(evidence or {}),
    )


def _input_weight(obs: NormalizedObservation, *, index: int, weights: dict[str, Any]) -> float:
    ref = obs.source_entity_id or f"input_{index}"
    value = weights.get(ref, weights.get(str(index), 1))
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 1.0


@dataclass(frozen=True)
class DirectFusionPlugin:
    plugin_id: str = "builtin.direct"
    plugin_api_version: int = 1
    supported_kinds: tuple[str, ...] = ()

    def derive(
        self,
        *,
        kind: str,
        inputs: list[NormalizedObservation],
        strategy_cfg: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> DerivedObservation:
        if not inputs:
            return _mk_derived(
                kind=kind,
                state="unknown",
                confidence=0,
                inputs=[],
                strategy="direct",
                plugin_id=self.plugin_id,
                reason="no_inputs",
                available=False,
            )
        base = inputs[0]
        return _mk_derived(
            kind=kind,
            state=base.state,
            confidence=base.confidence,
            inputs=inputs,
            strategy="direct",
            plugin_id=self.plugin_id,
            reason="pass_through",
            evidence={"selected_input": 0},
            available=base.available,
            stale=base.stale,
            raw_state=base.raw_state,
        )


@dataclass(frozen=True)
class BooleanSetFusionPlugin:
    """Built-ins for any_of/all_of/quorum on on/off/unknown observations."""

    plugin_id: str
    strategy_name: str
    plugin_api_version: int = 1
    supported_kinds: tuple[str, ...] = ("presence", "boolean_signal")

    def derive(
        self,
        *,
        kind: str,
        inputs: list[NormalizedObservation],
        strategy_cfg: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> DerivedObservation:
        strategy_cfg = dict(strategy_cfg or {})
        if not inputs:
            return _mk_derived(
                kind=kind,
                state="unknown",
                confidence=0,
                inputs=[],
                strategy=self.strategy_name,
                plugin_id=self.plugin_id,
                reason="no_inputs",
                available=False,
            )

        on_count = sum(1 for obs in inputs if obs.state == "on")
        off_count = sum(1 for obs in inputs if obs.state == "off")
        unknown_count = len(inputs) - on_count - off_count

        if self.strategy_name == "any_of":
            state = "on" if on_count > 0 else ("off" if unknown_count == 0 else "unknown")
            reason = "any_on" if on_count > 0 else ("all_off" if unknown_count == 0 else "unknown_inputs")
        elif self.strategy_name == "all_of":
            state = "off" if off_count > 0 else ("on" if unknown_count == 0 else "unknown")
            reason = "any_off" if off_count > 0 else ("all_on" if unknown_count == 0 else "unknown_inputs")
        elif self.strategy_name == "quorum":
            required = max(1, int(strategy_cfg.get("required", 1)))
            if on_count >= required:
                state = "on"
                reason = "quorum_reached"
            elif off_count == len(inputs):
                state = "off"
                reason = "all_off"
            else:
                state = "unknown"
                reason = "quorum_not_reached"
        else:
            raise ValueError(f"Unsupported built-in boolean fusion strategy: {self.strategy_name}")

        confidence = int((on_count / max(1, len(inputs))) * 100) if state == "on" else (100 if state == "off" else 0)
        return _mk_derived(
            kind=kind,
            state=state,
            confidence=confidence,
            inputs=inputs,
            strategy=self.strategy_name,
            plugin_id=self.plugin_id,
            reason=reason,
            evidence={
                "on_count": on_count,
                "off_count": off_count,
                "unknown_count": unknown_count,
                "required": strategy_cfg.get("required"),
            },
            available=all(obs.available for obs in inputs),
            stale=any(obs.stale for obs in inputs),
        )


@dataclass(frozen=True)
class WeightedQuorumFusionPlugin:
    """Built-in weighted quorum on on/off/unknown observations."""

    plugin_id: str = "builtin.weighted_quorum"
    plugin_api_version: int = 1
    supported_kinds: tuple[str, ...] = ("presence", "boolean_signal")

    def derive(
        self,
        *,
        kind: str,
        inputs: list[NormalizedObservation],
        strategy_cfg: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> DerivedObservation:
        strategy_cfg = dict(strategy_cfg or {})
        if not inputs:
            return _mk_derived(
                kind=kind,
                state="unknown",
                confidence=0,
                inputs=[],
                strategy="weighted_quorum",
                plugin_id=self.plugin_id,
                reason="no_inputs",
                available=False,
            )

        weights_cfg = dict(strategy_cfg.get("weights", {}))
        weighted_inputs = [
            (obs, _input_weight(obs, index=index, weights=weights_cfg))
            for index, obs in enumerate(inputs)
        ]
        total_weight = sum(weight for _, weight in weighted_inputs)
        threshold = strategy_cfg.get("threshold")
        if threshold is None:
            threshold_value = total_weight / 2.0 if total_weight > 0 else 0.0
        else:
            try:
                threshold_value = max(0.0, float(threshold))
            except (TypeError, ValueError):
                threshold_value = total_weight / 2.0 if total_weight > 0 else 0.0

        on_weight = sum(weight for obs, weight in weighted_inputs if obs.state == "on")
        off_weight = sum(weight for obs, weight in weighted_inputs if obs.state == "off")
        unknown_weight = sum(weight for obs, weight in weighted_inputs if obs.state not in {"on", "off"})

        if on_weight >= threshold_value:
            state = "on"
            reason = "weighted_threshold_reached"
        elif unknown_weight > 0:
            state = "unknown"
            reason = "weighted_threshold_not_reached_with_unknowns"
        else:
            state = "off"
            reason = "weighted_threshold_not_reached"

        if total_weight <= 0:
            confidence = 0
        elif state == "on":
            confidence = int((on_weight / total_weight) * 100)
        elif state == "off":
            confidence = int((off_weight / total_weight) * 100)
        else:
            confidence = 0

        return _mk_derived(
            kind=kind,
            state=state,
            confidence=confidence,
            inputs=inputs,
            strategy="weighted_quorum",
            plugin_id=self.plugin_id,
            reason=reason,
            evidence={
                "total_weight": total_weight,
                "threshold": threshold_value,
                "on_weight": on_weight,
                "off_weight": off_weight,
                "unknown_weight": unknown_weight,
                "weights": {
                    (obs.source_entity_id or f"input_{idx}"): weight
                    for idx, (obs, weight) in enumerate(weighted_inputs)
                },
            },
            available=all(obs.available for obs, _ in weighted_inputs),
            stale=any(obs.stale for obs, _ in weighted_inputs),
        )


def register_builtin_fusion_plugins(registry) -> None:
    """Register the built-in strategies into a registry instance."""
    registry.register(DirectFusionPlugin())
    registry.register(BooleanSetFusionPlugin(plugin_id="builtin.any_of", strategy_name="any_of"))
    registry.register(BooleanSetFusionPlugin(plugin_id="builtin.all_of", strategy_name="all_of"))
    registry.register(BooleanSetFusionPlugin(plugin_id="builtin.quorum", strategy_name="quorum"))
    registry.register(WeightedQuorumFusionPlugin())
