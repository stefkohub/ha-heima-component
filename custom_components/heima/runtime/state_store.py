"""Canonical state store for Heima entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CanonicalState:
    """In-memory canonical state for entities."""

    binary_sensors: dict[str, bool | None] = field(default_factory=dict)
    sensors: dict[str, Any] = field(default_factory=dict)
    selects: dict[str, str | None] = field(default_factory=dict)

    def get_binary(self, key: str) -> bool | None:
        return self.binary_sensors.get(key)

    def get_sensor(self, key: str) -> Any:
        return self.sensors.get(key)

    def get_select(self, key: str) -> str | None:
        return self.selects.get(key)

    def set_binary(self, key: str, value: bool | None) -> None:
        self.binary_sensors[key] = value

    def set_sensor(self, key: str, value: Any) -> None:
        self.sensors[key] = value

    def set_select(self, key: str, value: str) -> None:
        self.selects[key] = value
