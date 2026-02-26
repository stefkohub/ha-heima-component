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
        return self._fusion.derive(
            plugin_id=plugin_id,
            kind=kind,
            inputs=list(inputs),
            strategy_cfg=cfg,
            context=dict(context or {}),
        )

    def _read_state(self, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        state = self._hass.states.get(entity_id)
        return state.state if state else None

