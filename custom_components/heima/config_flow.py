"""Config flow for Heima."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import selector
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

import logging

from .const import (
    CONF_ENGINE_ENABLED,
    CONF_LANGUAGE,
    CONF_TIMEZONE,
    DEFAULT_ENGINE_ENABLED,
    DEFAULT_ENABLED_EVENT_CATEGORIES,
    DEFAULT_OCCUPANCY_MISMATCH_MIN_DERIVED_ROOMS,
    DEFAULT_OCCUPANCY_MISMATCH_PERSIST_S,
    DEFAULT_OCCUPANCY_MISMATCH_POLICY,
    DEFAULT_LIGHTING_APPLY_MODE,
    DOMAIN,
    EVENT_CATEGORIES_TOGGLEABLE,
    OCCUPANCY_MISMATCH_POLICIES,
    OPT_HEATING,
    OPT_LIGHTING_APPLY_MODE,
    OPT_LIGHTING_ROOMS,
    OPT_LIGHTING_ZONES,
    OPT_NOTIFICATIONS,
    OPT_PEOPLE_ANON,
    OPT_PEOPLE_NAMED,
    OPT_ROOMS,
    OPT_SECURITY,
)

PRESENCE_METHODS = ["ha_person", "quorum", "manual"]
ROOM_LOGIC = ["any_of", "all_of"]
ROOM_OCCUPANCY_MODES = ["derived", "none"]
HEATING_APPLY_MODES = ["delegate_to_scheduler", "set_temperature"]
LIGHTING_APPLY_MODES = ["scene", "delegate"]

_LOGGER = logging.getLogger(__name__)


def _default_timezone(hass: HomeAssistant) -> str:
    return str(getattr(hass.config, "time_zone", "UTC") or "UTC")


def _default_language(hass: HomeAssistant) -> str:
    return str(getattr(hass.config, "language", "en") or "en")


def _scene_selector(multiple: bool = False) -> dict[str, Any]:
    return selector({"entity": {"domain": "scene", "multiple": multiple}})


def _entity_selector(domains: list[str], multiple: bool = False) -> dict[str, Any]:
    return selector({"entity": {"domain": domains, "multiple": multiple}})


def _is_valid_slug(value: str) -> bool:
    try:
        cv.slug(value)
        return True
    except vol.Invalid:
        return False


_NON_NEGATIVE_INT = vol.All(vol.Coerce(int), vol.Range(min=0))


class HeimaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Heima."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Optional(CONF_ENGINE_ENABLED, default=DEFAULT_ENGINE_ENABLED): bool,
                }
            )
            return self.async_show_form(step_id="user", data_schema=schema)

        options = {
            CONF_ENGINE_ENABLED: user_input.get(CONF_ENGINE_ENABLED, DEFAULT_ENGINE_ENABLED),
            CONF_TIMEZONE: _default_timezone(self.hass),
            CONF_LANGUAGE: _default_language(self.hass),
            OPT_LIGHTING_APPLY_MODE: DEFAULT_LIGHTING_APPLY_MODE,
        }
        return self.async_create_entry(title="Heima", data={}, options=options)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return HeimaOptionsFlowHandler(config_entry)


class HeimaOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Heima options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self.options = dict(config_entry.options)
        self._editing_person_slug: str | None = None
        self._editing_room_id: str | None = None
        self._editing_zone_id: str | None = None
        self._editing_lighting_room_id: str | None = None

    # ---- Helpers ----
    def _people_named(self) -> list[dict[str, Any]]:
        return list(self.options.get(OPT_PEOPLE_NAMED, []))

    def _rooms(self) -> list[dict[str, Any]]:
        return list(self.options.get(OPT_ROOMS, []))

    def _lighting_rooms(self) -> list[dict[str, Any]]:
        return list(self.options.get(OPT_LIGHTING_ROOMS, []))

    def _lighting_zones(self) -> list[dict[str, Any]]:
        return list(self.options.get(OPT_LIGHTING_ZONES, []))

    def _room_ids(self) -> list[str]:
        return [room["room_id"] for room in self._rooms()]

    def _zone_ids(self) -> list[str]:
        return [zone["zone_id"] for zone in self._lighting_zones()]

    def _find_by_key(self, items: list[dict[str, Any]], key: str, value: str) -> dict[str, Any] | None:
        for item in items:
            if item.get(key) == value:
                return item
        return None

    def _store_list(self, key: str, items: list[dict[str, Any]]) -> None:
        self.options[key] = items

    def _with_suggested(self, schema: vol.Schema, defaults: dict[str, Any] | None) -> vol.Schema:
        """Populate form values without turning optional cleared fields into sticky defaults."""
        return self.add_suggested_values_to_schema(schema, defaults or {})

    def _normalize_multi_value(self, value: Any) -> list[str]:
        """Normalize selector/cv.multi_select outputs to a stable list[str]."""
        if value is None:
            return []
        if isinstance(value, dict):
            # Be defensive: some paths may return {id: true/false}.
            return [str(k) for k, enabled in value.items() if enabled]
        if isinstance(value, (list, tuple, set)):
            return [str(v) for v in value if str(v)]
        if isinstance(value, str):
            return [value] if value else []
        return [str(value)]

    def _normalize_people_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        data["slug"] = str(data.get("slug", "")).strip()
        data["display_name"] = str(data.get("display_name", "") or "").strip()
        data["presence_method"] = str(data.get("presence_method", "ha_person"))
        if data.get("person_entity"):
            data["person_entity"] = str(data["person_entity"])
        data["sources"] = self._normalize_multi_value(data.get("sources"))
        return data

    def _normalize_room_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        data["room_id"] = str(data.get("room_id", "")).strip()
        data["display_name"] = str(data.get("display_name", "") or "").strip()
        if data.get("area_id"):
            data["area_id"] = str(data["area_id"])
        data["sources"] = self._normalize_multi_value(data.get("sources"))
        occupancy_mode = str(data.get("occupancy_mode", "") or "").strip()
        if occupancy_mode not in ROOM_OCCUPANCY_MODES:
            occupancy_mode = "derived"
        data["occupancy_mode"] = occupancy_mode
        data["logic"] = str(data.get("logic", "any_of"))
        return data

    def _normalize_lighting_room_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        data["room_id"] = str(data.get("room_id", "")).strip()
        for key in ("scene_evening", "scene_relax", "scene_night", "scene_off"):
            if data.get(key):
                data[key] = str(data[key])
            elif key in data and data[key] in ("", []):
                data.pop(key, None)
        data["enable_manual_hold"] = bool(data.get("enable_manual_hold", True))
        return data

    def _normalize_lighting_zone_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        data["zone_id"] = str(data.get("zone_id", "")).strip()
        data["display_name"] = str(data.get("display_name", "") or "").strip()
        data["rooms"] = self._normalize_multi_value(data.get("rooms"))
        return data

    def _normalize_notifications_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        data["routes"] = self._normalize_multi_value(data.get("routes"))
        categories_present = "enabled_event_categories" in data
        categories = self._normalize_multi_value(data.get("enabled_event_categories"))
        if categories_present:
            data["enabled_event_categories"] = [
                c for c in categories if c in EVENT_CATEGORIES_TOGGLEABLE
            ]
        else:
            data["enabled_event_categories"] = list(DEFAULT_ENABLED_EVENT_CATEGORIES)
        policy = str(data.get("occupancy_mismatch_policy", DEFAULT_OCCUPANCY_MISMATCH_POLICY))
        if policy not in OCCUPANCY_MISMATCH_POLICIES:
            policy = DEFAULT_OCCUPANCY_MISMATCH_POLICY
        data["occupancy_mismatch_policy"] = policy
        data["occupancy_mismatch_min_derived_rooms"] = int(
            data.get(
                "occupancy_mismatch_min_derived_rooms",
                DEFAULT_OCCUPANCY_MISMATCH_MIN_DERIVED_ROOMS,
            )
        )
        data["occupancy_mismatch_persist_s"] = int(
            data.get("occupancy_mismatch_persist_s", DEFAULT_OCCUPANCY_MISMATCH_PERSIST_S)
        )
        return data

    def _error_if_immutable_changed(
        self, payload: dict[str, Any], field: str, expected_value: str | None
    ) -> dict[str, str]:
        if expected_value is None:
            return {}
        if str(payload.get(field, "")) != expected_value:
            return {field: "immutable"}
        return {}

    # ---- Flow steps ----
    async def async_step_init(self, user_input=None) -> FlowResult:
        return await self.async_step_general(user_input)

    async def async_step_general(self, user_input=None) -> FlowResult:
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ENGINE_ENABLED,
                    default=self.options.get(CONF_ENGINE_ENABLED, DEFAULT_ENGINE_ENABLED),
                ): bool,
                vol.Optional(
                    CONF_TIMEZONE,
                    default=self.options.get(CONF_TIMEZONE, _default_timezone(self.hass)),
                ): cv.string,
                vol.Optional(
                    CONF_LANGUAGE,
                    default=self.options.get(CONF_LANGUAGE, _default_language(self.hass)),
                ): cv.string,
                vol.Optional(
                    OPT_LIGHTING_APPLY_MODE,
                    default=self.options.get(
                        OPT_LIGHTING_APPLY_MODE, DEFAULT_LIGHTING_APPLY_MODE
                    ),
                ): vol.In(LIGHTING_APPLY_MODES),
            }
        )
        if user_input is None:
            return self.async_show_form(step_id="general", data_schema=schema)

        errors: dict[str, str] = {}
        timezone_value = user_input.get(CONF_TIMEZONE, _default_timezone(self.hass))
        if not dt_util.get_time_zone(timezone_value):
            errors[CONF_TIMEZONE] = "invalid_time_zone"

        if errors:
            return self.async_show_form(step_id="general", data_schema=schema, errors=errors)

        self.options[CONF_ENGINE_ENABLED] = user_input.get(
            CONF_ENGINE_ENABLED, DEFAULT_ENGINE_ENABLED
        )
        self.options[CONF_TIMEZONE] = timezone_value
        self.options[CONF_LANGUAGE] = user_input.get(CONF_LANGUAGE, _default_language(self.hass))
        self.options[OPT_LIGHTING_APPLY_MODE] = user_input.get(
            OPT_LIGHTING_APPLY_MODE, DEFAULT_LIGHTING_APPLY_MODE
        )
        return await self.async_step_people_menu()

    # ---- People (named + anonymous) ----
    async def async_step_people_menu(self, user_input=None) -> FlowResult:
        _LOGGER.debug("Options flow: people_menu")
        return self.async_show_menu(
            step_id="people_menu",
            menu_options=[
                "people_add",
                "people_edit",
                "people_remove",
                "people_anonymous",
                "people_save",
                "people_next",
            ],
        )

    async def async_step_people_add(self, user_input=None) -> FlowResult:
        _LOGGER.debug("Options flow: people_add user_input=%s", bool(user_input))
        errors: dict[str, str] = {}
        if user_input is None:
            return self.async_show_form(step_id="people_add", data_schema=self._people_schema())

        user_input = self._normalize_people_payload(user_input)
        errors = self._validate_people_payload(user_input, is_edit=False)
        if errors:
            return self.async_show_form(
                step_id="people_add", data_schema=self._people_schema(user_input), errors=errors
            )

        people = self._people_named()
        people.append(user_input)
        self._store_list(OPT_PEOPLE_NAMED, people)
        return await self.async_step_people_menu()

    async def async_step_people_edit(self, user_input=None) -> FlowResult:
        _LOGGER.debug("Options flow: people_edit user_input=%s", bool(user_input))
        people = self._people_named()
        if not people:
            return await self.async_step_people_add()

        if user_input is None:
            schema = vol.Schema({vol.Required("person"): vol.In([p["slug"] for p in people])})
            return self.async_show_form(step_id="people_edit", data_schema=schema)

        slug = user_input.get("person")
        self._editing_person_slug = slug
        return await self.async_step_people_edit_form()

    async def async_step_people_edit_form(self, user_input=None) -> FlowResult:
        _LOGGER.debug("Options flow: people_edit_form user_input=%s", bool(user_input))
        people = self._people_named()
        if user_input is None:
            existing = self._find_by_key(people, "slug", self._editing_person_slug or "") or {}
            return self.async_show_form(
                step_id="people_edit_form", data_schema=self._people_schema(existing)
            )

        user_input = self._normalize_people_payload(user_input)
        errors = self._validate_people_payload(user_input, is_edit=True)
        if errors:
            return self.async_show_form(
                step_id="people_edit_form", data_schema=self._people_schema(user_input), errors=errors
            )

        updated = []
        for person in people:
            if person.get("slug") == self._editing_person_slug:
                updated.append(user_input)
            else:
                updated.append(person)
        self._store_list(OPT_PEOPLE_NAMED, updated)
        self._editing_person_slug = None
        return await self.async_step_people_menu()

    async def async_step_people_remove(self, user_input=None) -> FlowResult:
        _LOGGER.debug("Options flow: people_remove user_input=%s", bool(user_input))
        people = self._people_named()
        if not people:
            return await self.async_step_people_add()

        if user_input is None:
            schema = vol.Schema({vol.Required("person"): vol.In([p["slug"] for p in people])})
            return self.async_show_form(step_id="people_remove", data_schema=schema)

        slug = user_input.get("person")
        updated = [p for p in people if p.get("slug") != slug]
        self._store_list(OPT_PEOPLE_NAMED, updated)
        return await self.async_step_people_menu()

    async def async_step_people_anonymous(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        current = dict(self.options.get(OPT_PEOPLE_ANON, {}))
        if user_input is None:
            return self.async_show_form(
                step_id="people_anonymous",
                data_schema=self._people_anonymous_schema(current),
            )

        user_input = dict(user_input)
        user_input["sources"] = self._normalize_multi_value(user_input.get("sources"))

        sources = user_input.get("sources", [])
        required = int(user_input.get("required", 1))
        if user_input.get("enabled") and not sources:
            errors["sources"] = "required"
        elif sources and required > len(sources):
            errors["required"] = "invalid_required"

        if errors:
            return self.async_show_form(
                step_id="people_anonymous",
                data_schema=self._people_anonymous_schema(user_input),
                errors=errors,
            )

        self.options[OPT_PEOPLE_ANON] = user_input
        return await self.async_step_rooms_menu()

    async def async_step_people_next(self, user_input=None) -> FlowResult:
        return await self.async_step_rooms_menu()

    async def async_step_people_save(self, user_input=None) -> FlowResult:
        """Persist options and close the flow from People menu."""
        return self.async_create_entry(title="", data=self._finalize_options())

    def _people_schema(self, defaults: dict[str, Any] | None = None) -> vol.Schema:
        defaults = defaults or {}
        schema = vol.Schema(
            {
                vol.Required("slug", default=defaults.get("slug", "")): cv.string,
                vol.Optional("display_name", default=defaults.get("display_name", "")):
                cv.string,
                vol.Required(
                    "presence_method", default=defaults.get("presence_method", "ha_person")
                ): vol.In(PRESENCE_METHODS),
                vol.Optional("person_entity"): _entity_selector(["person"]),
                vol.Optional("sources"): _entity_selector(
                    ["binary_sensor", "sensor", "device_tracker"], multiple=True
                ),
                vol.Optional("required", default=defaults.get("required", 1)): cv.positive_int,
                vol.Optional("arrive_hold_s", default=defaults.get("arrive_hold_s", 10)):
                cv.positive_int,
                vol.Optional("leave_hold_s", default=defaults.get("leave_hold_s", 120)):
                cv.positive_int,
                vol.Optional("enable_override", default=defaults.get("enable_override", False)): bool,
            }
        )
        return self._with_suggested(schema, defaults)

    def _people_anonymous_schema(self, defaults: dict[str, Any] | None = None) -> vol.Schema:
        defaults = defaults or {}
        schema = vol.Schema(
            {
                vol.Optional("enabled", default=defaults.get("enabled", False)): bool,
                vol.Optional("sources"): _entity_selector(
                    ["binary_sensor", "sensor", "device_tracker"], multiple=True
                ),
                vol.Optional("required", default=defaults.get("required", 1)): cv.positive_int,
                vol.Optional(
                    "anonymous_count_weight", default=defaults.get("anonymous_count_weight", 1)
                ): cv.positive_int,
                vol.Optional("arrive_hold_s", default=defaults.get("arrive_hold_s", 10)):
                cv.positive_int,
                vol.Optional("leave_hold_s", default=defaults.get("leave_hold_s", 120)):
                cv.positive_int,
            }
        )
        return self._with_suggested(schema, defaults)

    def _validate_people_payload(self, payload: dict[str, Any], is_edit: bool) -> dict[str, str]:
        errors: dict[str, str] = {}
        slug = payload.get("slug", "")
        if is_edit:
            errors.update(self._error_if_immutable_changed(payload, "slug", self._editing_person_slug))
        if not slug:
            errors["slug"] = "required"
        elif not _is_valid_slug(slug):
            errors["slug"] = "invalid_slug"
        if slug.startswith("heima_"):
            errors["slug"] = "reserved_prefix"

        existing_slugs = {p["slug"] for p in self._people_named()}
        if not is_edit:
            if slug in existing_slugs:
                errors["slug"] = "duplicate"
        elif slug in (existing_slugs - {self._editing_person_slug}):
            errors["slug"] = "duplicate"

        method = payload.get("presence_method")
        if method == "ha_person" and not payload.get("person_entity"):
            errors["person_entity"] = "required"
        if method == "quorum":
            sources = payload.get("sources", [])
            required = int(payload.get("required", 1))
            if not sources:
                errors["sources"] = "required"
            elif required > len(sources):
                errors["required"] = "invalid_required"
        return errors

    # ---- Rooms (occupancy) ----
    async def async_step_rooms_menu(self, user_input=None) -> FlowResult:
        return self.async_show_menu(
            step_id="rooms_menu",
            menu_options=[
                "rooms_add",
                "rooms_edit",
                "rooms_remove",
                "rooms_import_areas",
                "rooms_save",
                "rooms_next",
            ],
        )

    async def async_step_rooms_add(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is None:
            return self.async_show_form(step_id="rooms_add", data_schema=self._room_schema())

        user_input = self._normalize_room_payload(user_input)
        errors = self._validate_room_payload(user_input, is_edit=False)
        if errors:
            return self.async_show_form(
                step_id="rooms_add", data_schema=self._room_schema(user_input), errors=errors
            )

        rooms = self._rooms()
        rooms.append(user_input)
        self._store_list(OPT_ROOMS, rooms)
        return await self.async_step_rooms_menu()

    async def async_step_rooms_edit(self, user_input=None) -> FlowResult:
        rooms = self._rooms()
        if not rooms:
            return await self.async_step_rooms_menu()

        if user_input is None:
            schema = vol.Schema({vol.Required("room"): vol.In([r["room_id"] for r in rooms])})
            return self.async_show_form(step_id="rooms_edit", data_schema=schema)

        room_id = user_input.get("room")
        self._editing_room_id = room_id
        return await self.async_step_rooms_edit_form()

    async def async_step_rooms_edit_form(self, user_input=None) -> FlowResult:
        rooms = self._rooms()
        if user_input is None:
            existing = self._find_by_key(rooms, "room_id", self._editing_room_id or "") or {}
            return self.async_show_form(
                step_id="rooms_edit_form", data_schema=self._room_schema(existing)
            )

        user_input = self._normalize_room_payload(user_input)
        errors = self._validate_room_payload(user_input, is_edit=True)
        if errors:
            return self.async_show_form(
                step_id="rooms_edit_form", data_schema=self._room_schema(user_input), errors=errors
            )

        updated = []
        for room in rooms:
            if room.get("room_id") == self._editing_room_id:
                updated.append(user_input)
            else:
                updated.append(room)
        self._store_list(OPT_ROOMS, updated)
        self._editing_room_id = None
        return await self.async_step_rooms_menu()

    async def async_step_rooms_remove(self, user_input=None) -> FlowResult:
        rooms = self._rooms()
        if not rooms:
            return await self.async_step_rooms_menu()

        if user_input is None:
            schema = vol.Schema({vol.Required("room"): vol.In([r["room_id"] for r in rooms])})
            return self.async_show_form(step_id="rooms_remove", data_schema=schema)

        room_id = user_input.get("room")
        updated = [r for r in rooms if r.get("room_id") != room_id]
        self._store_list(OPT_ROOMS, updated)
        self._remove_lighting_room_mapping(room_id)
        self._remove_room_from_zones(room_id)
        return await self.async_step_rooms_menu()

    async def async_step_rooms_next(self, user_input=None) -> FlowResult:
        return await self.async_step_lighting_rooms_menu()

    async def async_step_rooms_save(self, user_input=None) -> FlowResult:
        """Persist options and close the flow from Rooms menu."""
        return self.async_create_entry(title="", data=self._finalize_options())

    async def async_step_rooms_import_areas(self, user_input=None) -> FlowResult:
        """Import HA areas as rooms (merge with existing)."""
        area_reg = ar.async_get(self.hass)
        rooms = self._rooms()
        existing_room_ids = {r.get("room_id") for r in rooms}
        existing_area_ids = {r.get("area_id") for r in rooms if r.get("area_id")}

        for area in area_reg.async_list_areas():
            if area.id in existing_area_ids:
                continue
            room_id = slugify(area.name)
            if room_id in existing_room_ids:
                continue
            rooms.append(
                {
                    "room_id": room_id,
                    "display_name": area.name,
                    "area_id": area.id,
                    "occupancy_mode": "none",
                    "sources": [],
                    "logic": "any_of",
                    "on_dwell_s": 5,
                    "off_dwell_s": 120,
                    "max_on_s": None,
                }
            )
            existing_room_ids.add(room_id)

        self._store_list(OPT_ROOMS, rooms)
        return await self.async_step_rooms_menu()

    def _room_schema(self, defaults: dict[str, Any] | None = None) -> vol.Schema:
        defaults = defaults or {}
        schema = vol.Schema(
            {
                vol.Required("room_id", default=defaults.get("room_id", "")): cv.string,
                vol.Optional("display_name", default=defaults.get("display_name", "")):
                cv.string,
                vol.Optional("area_id"): selector({"area": {}}),
                vol.Optional(
                    "occupancy_mode",
                    default=defaults.get("occupancy_mode", "derived"),
                ): vol.In(ROOM_OCCUPANCY_MODES),
                vol.Optional("sources"): _entity_selector(["binary_sensor", "sensor"], multiple=True),
                vol.Optional("logic", default=defaults.get("logic", "any_of")): vol.In(ROOM_LOGIC),
                vol.Optional("on_dwell_s", default=defaults.get("on_dwell_s", 5)): cv.positive_int,
                vol.Optional("off_dwell_s", default=defaults.get("off_dwell_s", 120)): cv.positive_int,
                vol.Optional("max_on_s", default=defaults.get("max_on_s")):
                vol.Any(None, cv.positive_int),
            }
        )
        return self._with_suggested(schema, defaults)

    def _validate_room_payload(self, payload: dict[str, Any], is_edit: bool) -> dict[str, str]:
        errors: dict[str, str] = {}
        room_id = payload.get("room_id", "")
        if is_edit:
            errors.update(self._error_if_immutable_changed(payload, "room_id", self._editing_room_id))
        if not room_id:
            errors["room_id"] = "required"
        elif not _is_valid_slug(room_id):
            errors["room_id"] = "invalid_slug"
        if room_id.startswith("heima_"):
            errors["room_id"] = "reserved_prefix"

        existing_ids = {r["room_id"] for r in self._rooms()}
        if not is_edit:
            if room_id in existing_ids:
                errors["room_id"] = "duplicate"
        elif room_id in (existing_ids - {self._editing_room_id}):
            errors["room_id"] = "duplicate"

        area_id = payload.get("area_id")
        if area_id:
            existing_area_ids = {r.get("area_id") for r in self._rooms() if r.get("area_id")}
            if is_edit:
                existing_room = self._find_by_key(self._rooms(), "room_id", self._editing_room_id or "")
                existing_area_id = existing_room.get("area_id") if existing_room else None
                existing_area_ids.discard(existing_area_id)
            if area_id in existing_area_ids:
                errors["area_id"] = "duplicate"

        occupancy_mode = str(payload.get("occupancy_mode", "derived"))
        if occupancy_mode not in ROOM_OCCUPANCY_MODES:
            errors["occupancy_mode"] = "invalid_option"

        sources = payload.get("sources", [])
        if occupancy_mode == "derived" and not sources:
            errors["sources"] = "required"
        return errors

    # ---- Lighting: per-room scenes ----
    async def async_step_lighting_rooms_menu(self, user_input=None) -> FlowResult:
        return self.async_show_menu(
            step_id="lighting_rooms_menu",
            menu_options=[
                "lighting_rooms_edit",
                "lighting_rooms_save",
                "lighting_rooms_next",
            ],
        )

    async def async_step_lighting_rooms_edit(self, user_input=None) -> FlowResult:
        room_ids = self._room_ids()
        if not room_ids:
            return await self.async_step_lighting_zones_menu()

        if user_input is None:
            schema = vol.Schema({vol.Required("room"): vol.In(room_ids)})
            return self.async_show_form(step_id="lighting_rooms_edit", data_schema=schema)

        self._editing_lighting_room_id = user_input.get("room")
        return await self.async_step_lighting_rooms_edit_form()

    async def async_step_lighting_rooms_edit_form(self, user_input=None) -> FlowResult:
        if user_input is None:
            existing = self._find_by_key(
                self._lighting_rooms(), "room_id", self._editing_lighting_room_id or ""
            ) or {"room_id": self._editing_lighting_room_id}
            return self.async_show_form(
                step_id="lighting_rooms_edit_form",
                data_schema=self._lighting_room_schema(existing),
            )

        user_input = self._normalize_lighting_room_payload(user_input)
        errors = self._validate_lighting_room_payload(user_input)
        if errors:
            return self.async_show_form(
                step_id="lighting_rooms_edit_form",
                data_schema=self._lighting_room_schema(user_input),
                errors=errors,
            )

        rooms = self._lighting_rooms()
        updated = [r for r in rooms if r.get("room_id") != user_input.get("room_id")]
        updated.append(user_input)
        self._store_list(OPT_LIGHTING_ROOMS, updated)
        self._editing_lighting_room_id = None
        return await self.async_step_lighting_rooms_menu()

    async def async_step_lighting_rooms_next(self, user_input=None) -> FlowResult:
        return await self.async_step_lighting_zones_menu()

    async def async_step_lighting_rooms_save(self, user_input=None) -> FlowResult:
        """Persist options and close the flow from Lighting Rooms menu."""
        return self.async_create_entry(title="", data=self._finalize_options())

    def _lighting_room_schema(self, defaults: dict[str, Any] | None = None) -> vol.Schema:
        defaults = defaults or {}
        schema = vol.Schema(
            {
                vol.Required("room_id", default=defaults.get("room_id", "")): cv.string,
                # No defaults on optional selector fields: using defaults here makes it
                # impossible to clear a scene, because the schema fills the old value back in.
                vol.Optional("scene_evening"): _scene_selector(),
                vol.Optional("scene_relax"): _scene_selector(),
                vol.Optional("scene_night"): _scene_selector(),
                vol.Optional("scene_off"): _scene_selector(),
                vol.Optional(
                    "enable_manual_hold", default=defaults.get("enable_manual_hold", True)
                ): bool,
            }
        )
        return self._with_suggested(schema, defaults)

    def _validate_lighting_room_payload(self, payload: dict[str, Any]) -> dict[str, str]:
        errors: dict[str, str] = {}
        if self._editing_lighting_room_id is not None:
            errors.update(
                self._error_if_immutable_changed(
                    payload, "room_id", self._editing_lighting_room_id
                )
            )
        if not payload.get("room_id"):
            errors["room_id"] = "required"
        elif not _is_valid_slug(payload.get("room_id", "")):
            errors["room_id"] = "invalid_slug"
        elif payload.get("room_id", "").startswith("heima_"):
            errors["room_id"] = "reserved_prefix"
        elif payload.get("room_id") not in set(self._room_ids()):
            errors["room_id"] = "unknown_room"

        return errors

    def _remove_lighting_room_mapping(self, room_id: str) -> None:
        rooms = [r for r in self._lighting_rooms() if r.get("room_id") != room_id]
        self._store_list(OPT_LIGHTING_ROOMS, rooms)

    # ---- Lighting: zones ----
    async def async_step_lighting_zones_menu(self, user_input=None) -> FlowResult:
        return self.async_show_menu(
            step_id="lighting_zones_menu",
            menu_options=[
                "lighting_zones_add",
                "lighting_zones_edit",
                "lighting_zones_remove",
                "lighting_zones_save",
                "lighting_zones_next",
            ],
        )

    async def async_step_lighting_zones_add(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is None:
            return self.async_show_form(
                step_id="lighting_zones_add", data_schema=self._lighting_zone_schema()
            )

        user_input = self._normalize_lighting_zone_payload(user_input)
        errors = self._validate_lighting_zone_payload(user_input, is_edit=False)
        if errors:
            return self.async_show_form(
                step_id="lighting_zones_add",
                data_schema=self._lighting_zone_schema(user_input),
                errors=errors,
            )

        zones = self._lighting_zones()
        zones.append(user_input)
        self._store_list(OPT_LIGHTING_ZONES, zones)
        return await self.async_step_lighting_zones_menu()

    async def async_step_lighting_zones_edit(self, user_input=None) -> FlowResult:
        zones = self._lighting_zones()
        if not zones:
            return await self.async_step_lighting_zones_menu()

        if user_input is None:
            schema = vol.Schema({vol.Required("zone"): vol.In([z["zone_id"] for z in zones])})
            return self.async_show_form(step_id="lighting_zones_edit", data_schema=schema)

        zone_id = user_input.get("zone")
        self._editing_zone_id = zone_id
        return await self.async_step_lighting_zones_edit_form()

    async def async_step_lighting_zones_edit_form(self, user_input=None) -> FlowResult:
        zones = self._lighting_zones()
        if user_input is None:
            existing = self._find_by_key(zones, "zone_id", self._editing_zone_id or "") or {}
            return self.async_show_form(
                step_id="lighting_zones_edit_form",
                data_schema=self._lighting_zone_schema(existing),
            )

        user_input = self._normalize_lighting_zone_payload(user_input)
        errors = self._validate_lighting_zone_payload(user_input, is_edit=True)
        if errors:
            return self.async_show_form(
                step_id="lighting_zones_edit_form",
                data_schema=self._lighting_zone_schema(user_input),
                errors=errors,
            )

        updated = []
        for zone in zones:
            if zone.get("zone_id") == self._editing_zone_id:
                updated.append(user_input)
            else:
                updated.append(zone)
        self._store_list(OPT_LIGHTING_ZONES, updated)
        self._editing_zone_id = None
        return await self.async_step_lighting_zones_menu()

    async def async_step_lighting_zones_remove(self, user_input=None) -> FlowResult:
        zones = self._lighting_zones()
        if not zones:
            return await self.async_step_lighting_zones_menu()

        if user_input is None:
            schema = vol.Schema({vol.Required("zone"): vol.In([z["zone_id"] for z in zones])})
            return self.async_show_form(step_id="lighting_zones_remove", data_schema=schema)

        zone_id = user_input.get("zone")
        updated = [z for z in zones if z.get("zone_id") != zone_id]
        self._store_list(OPT_LIGHTING_ZONES, updated)
        return await self.async_step_lighting_zones_menu()

    async def async_step_lighting_zones_next(self, user_input=None) -> FlowResult:
        return await self.async_step_heating(user_input)

    async def async_step_lighting_zones_save(self, user_input=None) -> FlowResult:
        """Persist options and close the flow from Lighting Zones menu."""
        return self.async_create_entry(title="", data=self._finalize_options())

    def _lighting_zone_schema(self, defaults: dict[str, Any] | None = None) -> vol.Schema:
        defaults = defaults or {}
        schema = vol.Schema(
            {
                vol.Required("zone_id", default=defaults.get("zone_id", "")): cv.string,
                vol.Optional("display_name", default=defaults.get("display_name", "")):
                cv.string,
                vol.Required("rooms"): cv.multi_select(self._room_ids()),
            }
        )
        return self._with_suggested(schema, defaults)

    def _validate_lighting_zone_payload(self, payload: dict[str, Any], is_edit: bool) -> dict[str, str]:
        errors: dict[str, str] = {}
        zone_id = payload.get("zone_id", "")
        if is_edit:
            errors.update(self._error_if_immutable_changed(payload, "zone_id", self._editing_zone_id))
        if not zone_id:
            errors["zone_id"] = "required"
        elif not _is_valid_slug(zone_id):
            errors["zone_id"] = "invalid_slug"
        if zone_id.startswith("heima_"):
            errors["zone_id"] = "reserved_prefix"

        existing_ids = {z["zone_id"] for z in self._lighting_zones()}
        if not is_edit:
            if zone_id in existing_ids:
                errors["zone_id"] = "duplicate"
        elif zone_id in (existing_ids - {self._editing_zone_id}):
            errors["zone_id"] = "duplicate"

        rooms = payload.get("rooms", [])
        if not rooms:
            errors["rooms"] = "required"
        else:
            unknown_rooms = [room_id for room_id in rooms if room_id not in set(self._room_ids())]
            if unknown_rooms:
                errors["rooms"] = "unknown_room"
        return errors

    def _remove_room_from_zones(self, room_id: str) -> None:
        zones = []
        for zone in self._lighting_zones():
            rooms = [r for r in zone.get("rooms", []) if r != room_id]
            if not rooms:
                continue
            updated = dict(zone)
            updated["rooms"] = rooms
            zones.append(updated)
        self._store_list(OPT_LIGHTING_ZONES, zones)

    # ---- Heating ----
    async def async_step_heating(self, user_input=None) -> FlowResult:
        current = dict(self.options.get(OPT_HEATING, {}))
        if user_input is None:
            return self.async_show_form(step_id="heating", data_schema=self._heating_schema(current))

        if user_input.get("apply_mode_auto") == "set_temperature" and not user_input.get("climate_entity"):
            return self.async_show_form(
                step_id="heating",
                data_schema=self._heating_schema(user_input),
                errors={"climate_entity": "required"},
            )

        self.options[OPT_HEATING] = user_input
        return await self.async_step_security()

    def _heating_schema(self, defaults: dict[str, Any]) -> vol.Schema:
        schema = vol.Schema(
            {
                vol.Optional("climate_entity"): _entity_selector(["climate"]),
                vol.Required(
                    "apply_mode_auto",
                    default=defaults.get("apply_mode_auto", "delegate_to_scheduler"),
                ): vol.In(HEATING_APPLY_MODES),
                vol.Optional("setpoint_eco", default=defaults.get("setpoint_eco", 18.0)):
                vol.Coerce(float),
                vol.Optional(
                    "setpoint_comfort", default=defaults.get("setpoint_comfort", 20.0)
                ): vol.Coerce(float),
                vol.Optional(
                    "setpoint_preheat", default=defaults.get("setpoint_preheat", 21.5)
                ): vol.Coerce(float),
                vol.Optional(
                    "min_seconds_between_commands",
                    default=defaults.get("min_seconds_between_commands", 120),
                ): cv.positive_int,
                vol.Optional(
                    "verify_after_s", default=defaults.get("verify_after_s", 15)
                ): cv.positive_int,
                vol.Optional("max_retries", default=defaults.get("max_retries", 2)):
                cv.positive_int,
            }
        )
        return self._with_suggested(schema, defaults)

    # ---- Security ----
    async def async_step_security(self, user_input=None) -> FlowResult:
        current = dict(self.options.get(OPT_SECURITY, {}))
        if user_input is None:
            return self.async_show_form(step_id="security", data_schema=self._security_schema(current))

        if user_input.get("enabled") and not user_input.get("security_state_entity"):
            return self.async_show_form(
                step_id="security",
                data_schema=self._security_schema(user_input),
                errors={"security_state_entity": "required"},
            )

        self.options[OPT_SECURITY] = user_input
        return await self.async_step_notifications()

    def _security_schema(self, defaults: dict[str, Any]) -> vol.Schema:
        schema = vol.Schema(
            {
                vol.Optional("enabled", default=defaults.get("enabled", False)): bool,
                vol.Optional("security_state_entity"): _entity_selector(
                    ["alarm_control_panel", "sensor", "binary_sensor"]
                ),
                vol.Optional(
                    "armed_away_value", default=defaults.get("armed_away_value", "armed_away")
                ): cv.string,
                vol.Optional(
                    "armed_home_value", default=defaults.get("armed_home_value", "armed_home")
                ): cv.string,
            }
        )
        return self._with_suggested(schema, defaults)

    # ---- Notifications ----
    async def async_step_notifications(self, user_input=None) -> FlowResult:
        current = dict(self.options.get(OPT_NOTIFICATIONS, {}))
        if user_input is None:
            return self.async_show_form(
                step_id="notifications", data_schema=self._notifications_schema(current)
            )

        user_input = self._normalize_notifications_payload(user_input)
        self.options[OPT_NOTIFICATIONS] = user_input
        return self.async_create_entry(title="", data=self._finalize_options())

    def _notify_services(self) -> list[str]:
        services = self.hass.services.async_services().get("notify", {})
        return sorted(services.keys())

    def _notify_service_choices(self, selected_routes: Any) -> list[str]:
        routes = self._normalize_multi_value(selected_routes)
        return sorted(set(self._notify_services()) | set(routes))

    def _notifications_schema(self, defaults: dict[str, Any] | None = None) -> vol.Schema:
        defaults = defaults or {}
        schema = vol.Schema(
            {
                vol.Optional("routes"): cv.multi_select(
                    self._notify_service_choices(defaults.get("routes", []))
                ),
                vol.Optional("enabled_event_categories"): cv.multi_select(
                    EVENT_CATEGORIES_TOGGLEABLE
                ),
                vol.Optional("dedup_window_s", default=defaults.get("dedup_window_s", 60)):
                _NON_NEGATIVE_INT,
                vol.Optional(
                    "rate_limit_per_key_s", default=defaults.get("rate_limit_per_key_s", 300)
                ): _NON_NEGATIVE_INT,
                vol.Optional(
                    "occupancy_mismatch_policy",
                    default=defaults.get(
                        "occupancy_mismatch_policy", DEFAULT_OCCUPANCY_MISMATCH_POLICY
                    ),
                ): vol.In(OCCUPANCY_MISMATCH_POLICIES),
                vol.Optional(
                    "occupancy_mismatch_min_derived_rooms",
                    default=defaults.get(
                        "occupancy_mismatch_min_derived_rooms",
                        DEFAULT_OCCUPANCY_MISMATCH_MIN_DERIVED_ROOMS,
                    ),
                ): _NON_NEGATIVE_INT,
                vol.Optional(
                    "occupancy_mismatch_persist_s",
                    default=defaults.get(
                        "occupancy_mismatch_persist_s", DEFAULT_OCCUPANCY_MISMATCH_PERSIST_S
                    ),
                ): _NON_NEGATIVE_INT,
            }
        )
        defaults_with_categories = dict(defaults)
        defaults_with_categories.setdefault(
            "enabled_event_categories", list(DEFAULT_ENABLED_EVENT_CATEGORIES)
        )
        return self._with_suggested(schema, defaults_with_categories)

    def _finalize_options(self) -> dict[str, Any]:
        """Return a coherent options snapshot before persisting."""
        options = dict(self.options)

        room_ids = {str(r.get("room_id")) for r in options.get(OPT_ROOMS, []) if r.get("room_id")}
        lighting_rooms = []
        for room_cfg in options.get(OPT_LIGHTING_ROOMS, []):
            room_id = str(room_cfg.get("room_id", "")).strip()
            if not room_id or room_id not in room_ids:
                continue
            lighting_rooms.append(self._normalize_lighting_room_payload(room_cfg))
        options[OPT_LIGHTING_ROOMS] = lighting_rooms

        lighting_zones = []
        for zone_cfg in options.get(OPT_LIGHTING_ZONES, []):
            zone = self._normalize_lighting_zone_payload(zone_cfg)
            zone["rooms"] = [room_id for room_id in zone.get("rooms", []) if room_id in room_ids]
            if not zone.get("rooms"):
                continue
            lighting_zones.append(zone)
        options[OPT_LIGHTING_ZONES] = lighting_zones

        if OPT_PEOPLE_NAMED in options:
            options[OPT_PEOPLE_NAMED] = [
                self._normalize_people_payload(person) for person in options.get(OPT_PEOPLE_NAMED, [])
            ]
        if OPT_ROOMS in options:
            normalized_rooms: list[dict[str, Any]] = []
            for room in options.get(OPT_ROOMS, []):
                room_norm = self._normalize_room_payload(room)
                # Backfill legacy rooms: empty sources usually indicate actuation-only rooms.
                if "occupancy_mode" not in room and not room_norm.get("sources"):
                    room_norm["occupancy_mode"] = "none"
                normalized_rooms.append(room_norm)
            options[OPT_ROOMS] = normalized_rooms
        if OPT_NOTIFICATIONS in options:
            options[OPT_NOTIFICATIONS] = self._normalize_notifications_payload(
                options.get(OPT_NOTIFICATIONS, {})
            )
        if OPT_PEOPLE_ANON in options:
            anon = dict(options.get(OPT_PEOPLE_ANON, {}))
            anon["sources"] = self._normalize_multi_value(anon.get("sources"))
            options[OPT_PEOPLE_ANON] = anon

        # Keep the in-memory working copy coherent for subsequent menu steps in the same flow.
        self.options = options
        return options
