"""Core runtime contracts for planning and events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class HeimaEvent:
    """Canonical event payload flowing through runtime."""

    type: str
    key: str
    severity: str
    title: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class ApplyStep:
    """Single desired apply action."""

    domain: str
    target: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class ApplyPlan:
    """Collection of apply actions for an evaluation cycle."""

    plan_id: str = field(default_factory=lambda: str(uuid4()))
    steps: list[ApplyStep] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "ApplyPlan":
        return cls(steps=[])
