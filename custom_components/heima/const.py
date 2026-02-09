"""Constants for the Heima integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "heima"
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
]

# Options keys (v1)
CONF_ENGINE_ENABLED = "engine_enabled"
CONF_TIMEZONE = "timezone"
CONF_LANGUAGE = "language"

OPT_PEOPLE_NAMED = "people_named"
OPT_PEOPLE_ANON = "people_anonymous"
OPT_ROOMS = "rooms"
OPT_LIGHTING_ROOMS = "lighting_rooms"
OPT_LIGHTING_ZONES = "lighting_zones"
OPT_LIGHTING_APPLY_MODE = "lighting_apply_mode"
OPT_HEATING = "heating"
OPT_SECURITY = "security"
OPT_NOTIFICATIONS = "notifications"

DEFAULT_ENGINE_ENABLED = True
DEFAULT_LIGHTING_APPLY_MODE = "scene"

# Services
SERVICE_COMMAND = "command"
SERVICE_SET_MODE = "set_mode"
SERVICE_SET_OVERRIDE = "set_override"

# Events
EVENT_HEIMA_EVENT = "heima_event"
EVENT_HEIMA_SNAPSHOT = "heima_snapshot"
EVENT_HEIMA_HEALTH = "heima_health"

DIAGNOSTICS_REDACT_KEYS = {
    "latitude",
    "longitude",
    "gps",
    "device_id",
    "entity_id",
}
