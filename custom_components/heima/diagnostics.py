"""Diagnostics support for Heima."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .const import DIAGNOSTICS_REDACT_KEYS, DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = data.get("coordinator")

    payload = {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "minor_version": getattr(entry, "minor_version", None),
            "options": dict(entry.options),
        },
        "runtime": {
            "data": getattr(coordinator, "data", None),
        },
    }

    return async_redact_data(payload, DIAGNOSTICS_REDACT_KEYS)
