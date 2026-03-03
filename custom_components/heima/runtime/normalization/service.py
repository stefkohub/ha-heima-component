"""Runtime facade for input normalization (N1 foundation)."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .builtins import register_builtin_fusion_plugins
from .contracts import DerivedObservation, NormalizedObservation, build_observation
from .registry import NormalizationFusionRegistry

_PRESENCE_ON_STATES = {
    "on",
    "home",
    "open",
    "occupied",
    "detected",
    "true",
    "1",
}


class InputNormalizer:
    """Single entry point for raw->normalized observations and signal fusion."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        fusion_registry: NormalizationFusionRegistry | None = None,
    ) -> None:
        self._hass = hass
        self._fusion = fusion_registry or NormalizationFusionRegistry()
        if not self._fusion.descriptors():
            register_builtin_fusion_plugins(self._fusion)
        self._derive_calls = 0
        self._derive_fallback_unknown = 0
        self._derive_plugin_errors = 0
        self._derive_plugin_error_counts: dict[str, int] = {}
        self._last_plugin_error: dict[str, Any] | None = None
        self._last_derive: dict[str, Any] | None = None

    @property
    def fusion_registry(self) -> NormalizationFusionRegistry:
        return self._fusion

    def presence(self, entity_id: str | None) -> NormalizedObservation:
        raw = self._read_state(entity_id)
        if not entity_id:
            return build_observation(
                kind="presence",
                state="unknown",
                confidence=0,
                raw_state=None,
                source_entity_id=None,
                available=False,
                reason="missing_entity_id",
            )
        if raw is None:
            return build_observation(
                kind="presence",
                state="unknown",
                confidence=0,
                raw_state=None,
                source_entity_id=entity_id,
                available=False,
                reason="entity_not_found",
            )
        lowered = raw.lower()
        if lowered in {"unknown", "unavailable"}:
            return build_observation(
                kind="presence",
                state="unknown",
                confidence=0,
                raw_state=raw,
                source_entity_id=entity_id,
                available=(lowered != "unavailable"),
                reason=lowered,
            )
        if lowered in _PRESENCE_ON_STATES:
            return build_observation(
                kind="presence",
                state="on",
                confidence=100,
                raw_state=raw,
                source_entity_id=entity_id,
                reason="state_match_on",
            )
        try:
            if float(raw) > 0:
                return build_observation(
                    kind="presence",
                    state="on",
                    confidence=100,
                    raw_state=raw,
                    source_entity_id=entity_id,
                    reason="numeric_gt_zero",
                )
        except ValueError:
            pass
        return build_observation(
            kind="presence",
            state="off",
            confidence=100,
            raw_state=raw,
            source_entity_id=entity_id,
            reason="default_off",
        )

    def boolean_signal(self, entity_id: str | None) -> NormalizedObservation:
        raw = self._read_state(entity_id)
        if not entity_id:
            return build_observation(
                kind="boolean_signal",
                state="unknown",
                confidence=0,
                raw_state=None,
                source_entity_id=None,
                available=False,
                reason="missing_entity_id",
            )
        if raw is None:
            return build_observation(
                kind="boolean_signal",
                state="unknown",
                confidence=0,
                raw_state=None,
                source_entity_id=entity_id,
                available=False,
                reason="entity_not_found",
            )
        lowered = raw.lower()
        if lowered in {"unknown", "unavailable"}:
            return build_observation(
                kind="boolean_signal",
                state="unknown",
                confidence=0,
                raw_state=raw,
                source_entity_id=entity_id,
                available=(lowered != "unavailable"),
                reason=lowered,
            )
        # Intentionally keep behavior aligned with legacy truthy parser in N1.
        if lowered in _PRESENCE_ON_STATES:
            return build_observation(
                kind="boolean_signal",
                state="on",
                confidence=100,
                raw_state=raw,
                source_entity_id=entity_id,
                reason="state_match_on",
            )
        try:
            if float(raw) > 0:
                return build_observation(
                    kind="boolean_signal",
                    state="on",
                    confidence=100,
                    raw_state=raw,
                    source_entity_id=entity_id,
                    reason="numeric_gt_zero",
                )
        except ValueError:
            pass
        return build_observation(
            kind="boolean_signal",
            state="off",
            confidence=100,
            raw_state=raw,
            source_entity_id=entity_id,
            reason="default_off",
        )

    def boolean_value(
        self,
        value: bool,
        *,
        source_key: str,
        reason: str,
        confidence: int = 100,
    ) -> NormalizedObservation:
        """Create a normalized boolean observation from a runtime-derived fact."""
        return build_observation(
            kind="boolean_signal",
            state="on" if bool(value) else "off",
            confidence=confidence,
            raw_state="on" if bool(value) else "off",
            source_entity_id=source_key,
            reason=reason,
        )

    def security(self, entity_id: str | None, mapping_cfg: dict[str, Any] | None = None) -> NormalizedObservation:
        mapping_cfg = dict(mapping_cfg or {})
        raw = self._read_state(entity_id)
        if not entity_id:
            return build_observation(
                kind="security",
                state="unknown",
                confidence=0,
                raw_state=None,
                source_entity_id=None,
                available=False,
                reason="missing_entity_id",
            )
        if raw is None:
            return build_observation(
                kind="security",
                state="unknown",
                confidence=0,
                raw_state=None,
                source_entity_id=entity_id,
                available=False,
                reason="entity_not_found",
            )

        lowered = raw.lower()
        armed_away_value = str(mapping_cfg.get("armed_away_value", "armed_away")).lower()
        armed_home_value = str(mapping_cfg.get("armed_home_value", "armed_home")).lower()

        if lowered == "unavailable":
            state, reason, available = "unavailable", "raw_unavailable", False
        elif lowered == "unknown":
            state, reason, available = "unknown", "raw_unknown", True
        elif lowered == armed_away_value:
            state, reason, available = "armed_away", "mapped_armed_away", True
        elif lowered == armed_home_value:
            state, reason, available = "armed_home", "mapped_armed_home", True
        elif lowered == "disarmed":
            state, reason, available = "disarmed", "raw_disarmed", True
        elif lowered in {"arming", "pending", "triggered"}:
            state, reason, available = "transition", f"raw_{lowered}", True
        else:
            state, reason, available = "unknown", "unmapped_raw_state", True

        return build_observation(
            kind="security",
            state=state,
            confidence=100 if state not in {"unknown", "unavailable"} else 0,
            raw_state=raw,
            source_entity_id=entity_id,
            available=available,
            reason=reason,
        )

    def derive(
        self,
        *,
        kind: str,
        inputs: list[NormalizedObservation],
        strategy_cfg: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> DerivedObservation:
        cfg = dict(strategy_cfg or {})
        plugin_id = str(cfg.pop("plugin_id", "builtin.direct"))
        fallback_state = self._normalize_fallback_state(cfg.pop("fallback_state", "unknown"))
        normalized_inputs = list(inputs)
        self._derive_calls += 1
        try:
            result = self._fusion.derive(
                plugin_id=plugin_id,
                kind=kind,
                inputs=normalized_inputs,
                strategy_cfg=cfg,
                context=dict(context or {}),
            )
        except Exception as err:
            self._derive_plugin_errors += 1
            self._derive_fallback_unknown += 1
            self._derive_plugin_error_counts[plugin_id] = self._derive_plugin_error_counts.get(plugin_id, 0) + 1
            self._last_plugin_error = {
                "plugin_id": plugin_id,
                "kind": kind,
                "error_type": type(err).__name__,
                "error": str(err),
            }
            result = self._fallback_derived_unknown(
                kind=kind,
                inputs=normalized_inputs,
                plugin_id=plugin_id,
                error=err,
                fallback_state=fallback_state,
            )

        self._last_derive = {
            "plugin_id": plugin_id,
            "kind": kind,
            "result_state": result.state,
            "result_reason": result.reason,
            "used_fallback": result.reason == "plugin_error_fallback",
            "fallback_state": result.evidence.get("fallback") if isinstance(result.evidence, dict) else None,
        }
        return result

    def _read_state(self, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        state = self._hass.states.get(entity_id)
        return state.state if state else None

    def diagnostics(self) -> dict[str, Any]:
        return {
            "derive_calls": self._derive_calls,
            "derive_fallback_unknown": self._derive_fallback_unknown,
            "derive_plugin_errors": self._derive_plugin_errors,
            "derive_plugin_error_counts": dict(self._derive_plugin_error_counts),
            "last_plugin_error": dict(self._last_plugin_error) if self._last_plugin_error else None,
            "last_derive": dict(self._last_derive) if self._last_derive else None,
            "registered_plugins": [
                {
                    "plugin_id": descriptor.plugin_id,
                    "plugin_api_version": descriptor.plugin_api_version,
                    "supported_kinds": list(descriptor.supported_kinds),
                }
                for descriptor in self._fusion.descriptors()
            ],
        }

    def _fallback_derived_unknown(
        self,
        *,
        kind: str,
        inputs: list[NormalizedObservation],
        plugin_id: str,
        error: Exception,
        fallback_state: str,
    ) -> DerivedObservation:
        return DerivedObservation(
            kind=kind,
            state=fallback_state,
            confidence=0,
            raw_state=None,
            source_entity_id=None,
            available=False,
            stale=any(obs.stale for obs in inputs),
            reason="plugin_error_fallback",
            inputs=[obs.source_entity_id or f"{obs.kind}:{obs.state}" for obs in inputs],
            fusion_strategy=f"fallback_{fallback_state}",
            plugin_id=plugin_id,
            plugin_api_version=1,
            evidence={
                "fallback": fallback_state,
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )

    def _normalize_fallback_state(self, value: Any) -> str:
        lowered = str(value or "unknown").strip().lower()
        if lowered in {"on", "off", "unknown"}:
            return lowered
        return "unknown"
