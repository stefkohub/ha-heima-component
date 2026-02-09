"""Typed models for Heima configuration and runtime state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_ENGINE_ENABLED,
    CONF_LANGUAGE,
    CONF_TIMEZONE,
    DEFAULT_ENGINE_ENABLED,
)


@dataclass(frozen=True)
class HeimaOptions:
    """Normalized options stored in the config entry."""

    engine_enabled: bool
    timezone: str
    language: str

    @classmethod
    def from_entry(cls, entry: ConfigEntry) -> "HeimaOptions":
        options: dict[str, Any] = dict(entry.options)
        return cls(
            engine_enabled=bool(options.get(CONF_ENGINE_ENABLED, DEFAULT_ENGINE_ENABLED)),
            timezone=str(options.get(CONF_TIMEZONE, "UTC") or "UTC"),
            language=str(options.get(CONF_LANGUAGE, "en") or "en"),
        )


@dataclass(frozen=True)
class HeimaRuntimeState:
    """Minimal runtime state surfaced to entity platforms."""

    health_ok: bool
    health_reason: str
    house_state: str
    house_state_reason: str
    last_decision: str
    last_action: str
