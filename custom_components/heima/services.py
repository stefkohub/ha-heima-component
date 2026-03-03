"""Service registration for Heima."""

from __future__ import annotations

import logging
from typing import Any
from collections.abc import Iterable

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    HOUSE_STATES_CANONICAL,
    SERVICE_COMMAND,
    SERVICE_SET_MODE,
    SERVICE_SET_OVERRIDE,
)
from .coordinator import HeimaCoordinator

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
    "set_security_intent",
    "set_room_lighting_hold",
    "notify_event",
}


def _validate_command(command: str) -> None:
    if command not in SUPPORTED_COMMANDS:
        raise ServiceValidationError(f"Unsupported heima.command '{command}'")


def _iter_coordinators(hass: HomeAssistant) -> Iterable[HeimaCoordinator]:
    domain_data = hass.data.get(DOMAIN, {})
    for value in domain_data.values():
        if isinstance(value, dict) and "coordinator" in value:
            coordinator = value["coordinator"]
            if isinstance(coordinator, HeimaCoordinator):
                yield coordinator


def _coordinators_for_target(hass: HomeAssistant, target: dict[str, Any]) -> list[HeimaCoordinator]:
    coordinators = list(_iter_coordinators(hass))
    entry_id = target.get("entry_id")
    if not entry_id:
        return coordinators
    return [c for c in coordinators if c.entry.entry_id == entry_id]


def _require_target_value(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if value in (None, ""):
        raise ServiceValidationError(f"Missing required target field '{key}'")
    return str(value)


async def _evaluate_all(coordinators: list[HeimaCoordinator], reason: str) -> None:
    for coordinator in coordinators:
        await coordinator.async_request_evaluation(reason=reason)


async def _set_select_and_evaluate(
    coordinators: list[HeimaCoordinator],
    *,
    select_key: str,
    option: str,
    reason: str,
) -> None:
    matched = False
    for coordinator in coordinators:
        current = coordinator.engine.state.get_select(select_key)
        if current is None:
            continue
        coordinator.engine.state.set_select(select_key, option)
        await coordinator.async_request_evaluation(reason=reason)
        matched = True
    if not matched:
        raise ServiceValidationError(f"Heima select not found: '{select_key}'")


async def _set_binary_and_evaluate(
    coordinators: list[HeimaCoordinator],
    *,
    binary_key: str,
    state: bool,
    reason: str,
) -> None:
    matched = False
    for coordinator in coordinators:
        if binary_key not in coordinator.engine.state.binary_sensors:
            continue
        coordinator.engine.state.set_binary(binary_key, state)
        await coordinator.async_request_evaluation(reason=reason)
        matched = True
    if not matched:
        raise ServiceValidationError(f"Heima binary sensor not found: '{binary_key}'")


async def async_register_services(hass: HomeAssistant) -> None:
    async def _handle_command(call: ServiceCall) -> None:
        data: dict[str, Any] = call.data
        command = str(data.get("command"))
        _validate_command(command)
        target = dict(data.get("target", {}))
        params = dict(data.get("params", {}))
        coordinators = _coordinators_for_target(hass, target)
        if not coordinators:
            raise ServiceValidationError("No active Heima config entries found for target")

        if command == "recompute_now":
            await _evaluate_all(coordinators, reason="service:recompute_now")
            return

        if command == "set_lighting_intent":
            zone_id = _require_target_value(target, "zone_id")
            option = str(params.get("intent") or target.get("intent") or "")
            if not option:
                raise ServiceValidationError("Missing lighting intent value ('intent')")
            await _set_select_and_evaluate(
                coordinators,
                select_key=f"heima_lighting_intent_{zone_id}",
                option=option,
                reason=f"service:set_lighting_intent:{zone_id}:{option}",
            )
            return

        if command == "set_security_intent":
            option = str(params.get("intent") or target.get("intent") or "")
            if not option:
                raise ServiceValidationError("Missing security intent value ('intent')")
            await _set_select_and_evaluate(
                coordinators,
                select_key="heima_security_intent",
                option=option,
                reason=f"service:set_security_intent:{option}",
            )
            return

        if command == "set_room_lighting_hold":
            room_id = _require_target_value(target, "room_id")
            state = params.get("state", target.get("state"))
            if state is None:
                raise ServiceValidationError("Missing hold state ('state')")
            await _set_binary_and_evaluate(
                coordinators,
                binary_key=f"heima_lighting_manual_hold_{room_id}",
                state=bool(state),
                reason=f"service:set_room_lighting_hold:{room_id}:{bool(state)}",
            )
            return

        if command == "notify_event":
            event_type = str(params.get("type", "custom.notify_event"))
            key = str(params.get("key", "custom.notify_event"))
            severity = str(params.get("severity", "info"))
            title = str(params.get("title", "Heima event"))
            message = str(params.get("message", ""))
            context = dict(params.get("context", {}))
            if data.get("request_id"):
                context.setdefault("request_id", str(data.get("request_id")))

            for coordinator in coordinators:
                await coordinator.async_emit_event(
                    event_type=event_type,
                    key=key,
                    severity=severity,
                    title=title,
                    message=message,
                    context=context,
                    reason="service:notify_event",
                )
            return

    async def _handle_set_mode(call: ServiceCall) -> None:
        payload = dict(call.data)
        mode = str(payload.get("mode", "")).strip()
        if mode not in HOUSE_STATES_CANONICAL:
            raise ServiceValidationError(f"Unsupported house-state override mode '{mode}'")
        state = bool(payload.get("state"))
        coordinators = list(_iter_coordinators(hass))
        if not coordinators:
            raise ServiceValidationError("No active Heima config entries found")
        for coordinator in coordinators:
            await coordinator.async_set_house_state_override(mode=mode, enabled=state)

    async def _handle_set_override(call: ServiceCall) -> None:
        payload = dict(call.data)
        scope = str(payload.get("scope"))
        item_id = str(payload.get("id"))
        override = payload.get("override")
        coordinators = list(_iter_coordinators(hass))
        if not coordinators:
            raise ServiceValidationError("No active Heima config entries found")

        if scope == "lighting_room_hold":
            await _set_binary_and_evaluate(
                coordinators,
                binary_key=f"heima_lighting_manual_hold_{item_id}",
                state=bool(override),
                reason=f"service:set_override:lighting_room_hold:{item_id}:{bool(override)}",
            )
            return

        if scope == "person":
            await _set_select_and_evaluate(
                coordinators,
                select_key=f"heima_person_{item_id}_override",
                option=str(override),
                reason=f"service:set_override:person:{item_id}:{override}",
            )
            return

        raise ServiceValidationError(f"Unsupported override scope '{scope}'")

    hass.services.async_register(DOMAIN, SERVICE_COMMAND, _handle_command, schema=COMMAND_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_MODE, _handle_set_mode, schema=SET_MODE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_SET_OVERRIDE, _handle_set_override, schema=SET_OVERRIDE_SCHEMA
    )
