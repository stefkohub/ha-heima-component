"""Service registration for Heima."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    EVENT_HEIMA_EVENT,
    SERVICE_COMMAND,
    SERVICE_SET_MODE,
    SERVICE_SET_OVERRIDE,
)

_LOGGER = logging.getLogger(__name__)

COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required("command"): cv.string,
        vol.Optional("target", default={}): dict,
        vol.Optional("params", default={}): dict,
        vol.Optional("request_id"): cv.string,
    }
)

SET_MODE_SCHEMA = vol.Schema(
    {
        vol.Required("mode"): cv.string,
        vol.Required("state"): cv.boolean,
    }
)

SET_OVERRIDE_SCHEMA = vol.Schema(
    {
        vol.Required("scope"): cv.string,
        vol.Required("id"): cv.string,
        vol.Required("override"): vol.Any(cv.string, cv.boolean),
    }
)

SUPPORTED_COMMANDS = {
    "recompute_now",
    "set_lighting_intent",
    "set_heating_intent",
    "set_security_intent",
    "set_room_lighting_hold",
    "notify_event",
}


def _validate_command(command: str) -> None:
    if command not in SUPPORTED_COMMANDS:
        raise ServiceValidationError(f"Unsupported heima.command '{command}'")


async def async_register_services(hass: HomeAssistant) -> None:
    async def _handle_command(call: ServiceCall) -> None:
        data: dict[str, Any] = call.data
        command = str(data.get("command"))
        _validate_command(command)

        if command == "recompute_now":
            hass.bus.async_fire(
                EVENT_HEIMA_EVENT,
                {
                    "type": "system.command_received",
                    "key": "system.command_received.recompute_now",
                    "severity": "info",
                    "title": "Heima command received",
                    "message": "recompute_now",
                    "context": {"command": command},
                    "event_id": data.get("request_id", ""),
                    "ts": "",
                },
            )
            return

        _LOGGER.info("Heima command accepted (placeholder): %s", command)

    async def _handle_set_mode(call: ServiceCall) -> None:
        _LOGGER.info("Heima set_mode called: %s", call.data)

    async def _handle_set_override(call: ServiceCall) -> None:
        _LOGGER.info("Heima set_override called: %s", call.data)

    hass.services.async_register(DOMAIN, SERVICE_COMMAND, _handle_command, schema=COMMAND_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_MODE, _handle_set_mode, schema=SET_MODE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_SET_OVERRIDE, _handle_set_override, schema=SET_OVERRIDE_SCHEMA
    )
