"""Contracts for Heima input normalization and signal fusion."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class NormalizedObservation:
    """Canonical normalized observation produced by the normalization layer."""

    kind: str
    state: str
    confidence: int
    raw_state: str | None
    source_entity_id: str | None
    ts: str = field(default_factory=_utc_now_iso)
    stale: bool = False
    available: bool = True
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DerivedObservation(NormalizedObservation):
    """Normalized observation produced by a fusion strategy/plugin."""

    inputs: list[str] = field(default_factory=list)
    fusion_strategy: str = "direct"
    plugin_id: str = "builtin.direct"
    plugin_api_version: int = 1
    evidence: dict[str, Any] = field(default_factory=dict)


def build_observation(
    *,
    kind: str,
    state: str,
    confidence: int,
    raw_state: str | None,
    source_entity_id: str | None,
    stale: bool = False,
    available: bool = True,
    reason: str = "",
) -> NormalizedObservation:
    """Helper to create a normalized observation with clamped confidence."""
    return NormalizedObservation(
        kind=kind,
        state=state,
        confidence=max(0, min(100, int(confidence))),
        raw_state=raw_state,
        source_entity_id=source_entity_id,
        stale=bool(stale),
        available=bool(available),
        reason=reason,
    )

