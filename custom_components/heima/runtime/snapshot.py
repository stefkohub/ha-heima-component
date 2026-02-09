"""Decision snapshot models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class DecisionSnapshot:
    """Minimal decision snapshot (v1)."""

    snapshot_id: str
    ts: str
    house_state: str
    anyone_home: bool
    people_count: int
    occupied_rooms: list[str]
    lighting_intents: dict[str, str]
    heating_intent: str
    security_state: str
    notes: str | None = None

    @classmethod
    def empty(cls) -> "DecisionSnapshot":
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            snapshot_id="",
            ts=now,
            house_state="unknown",
            anyone_home=False,
            people_count=0,
            occupied_rooms=[],
            lighting_intents={},
            heating_intent="auto",
            security_state="unknown",
            notes=None,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
