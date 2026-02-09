"""Heima runtime engine (core + lighting v1)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from ..const import (
    DEFAULT_LIGHTING_APPLY_MODE,
    OPT_LIGHTING_APPLY_MODE,
    OPT_LIGHTING_ROOMS,
    OPT_LIGHTING_ZONES,
    OPT_PEOPLE_ANON,
    OPT_PEOPLE_NAMED,
    OPT_ROOMS,
    OPT_SECURITY,
)
from ..entities.registry import build_registry
from ..models import HeimaOptions
from .contracts import ApplyPlan, ApplyStep
from .lighting import pick_scene_for_intent, resolve_zone_intent
from .policy import resolve_house_state
from .snapshot import DecisionSnapshot
from .state_store import CanonicalState

_LOGGER = logging.getLogger(__name__)

_PRESENCE_ON_STATES = {
    "on",
    "home",
    "open",
    "occupied",
    "detected",
    "true",
    "1",
}

_HOUSE_SIGNAL_ENTITIES = {
    "input_boolean.vacation_mode",
    "input_boolean.guest_mode",
    "binary_sensor.sleep_window",
    "binary_sensor.relax_mode",
    "binary_sensor.work_window",
}

_LIGHTING_MIN_SECONDS_BETWEEN_APPLIES = 10


@dataclass(frozen=True)
class EngineHealth:
    """Health status for the runtime engine."""

    ok: bool
    reason: str


class HeimaEngine:
    """Core runtime engine with canonical compute pipeline."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._options = HeimaOptions.from_entry(entry)
        self._health = EngineHealth(ok=True, reason="initialized")
        self._snapshot = DecisionSnapshot.empty()
        self._state = CanonicalState()
        self._apply_plan = ApplyPlan.empty()
        self._lighting_last_scene: dict[str, str] = {}
        self._lighting_last_ts: dict[str, float] = {}

    @property
    def health(self) -> EngineHealth:
        return self._health

    @property
    def snapshot(self) -> DecisionSnapshot:
        return self._snapshot

    @property
    def state(self) -> CanonicalState:
        return self._state

    async def async_initialize(self) -> None:
        _LOGGER.debug("Heima engine initialize")
        self._options = HeimaOptions.from_entry(self._entry)
        self._health = EngineHealth(ok=True, reason="initialized")
        self._build_default_state()
        await self.async_evaluate(reason="initialize")

    async def async_shutdown(self) -> None:
        _LOGGER.debug("Heima engine shutdown")
        self._health = EngineHealth(ok=True, reason="shutdown")

    async def async_reload_options(self, entry: ConfigEntry) -> None:
        _LOGGER.debug("Heima engine reload options")
        self._entry = entry
        self._options = HeimaOptions.from_entry(entry)
        self._build_default_state()
        await self.async_evaluate(reason="options_reloaded")

    async def async_evaluate(self, reason: str) -> DecisionSnapshot:
        """Evaluate canonical state from configured bindings."""
        _LOGGER.debug("Heima evaluation requested: %s", reason)
        snapshot = self._compute_snapshot(reason=reason)
        self._snapshot = snapshot
        self._apply_snapshot_to_canonical_state(snapshot)

        plan = self._build_apply_plan(snapshot)
        self._apply_plan = plan

        if self._options.engine_enabled and self._lighting_apply_mode() == "scene":
            await self._execute_apply_plan(plan)

        return snapshot

    def tracked_entity_ids(self) -> set[str]:
        """Entities that should trigger recomputation on state change."""
        options = dict(self._entry.options)
        tracked: set[str] = set(_HOUSE_SIGNAL_ENTITIES)

        for person in options.get(OPT_PEOPLE_NAMED, []):
            entity = person.get("person_entity")
            if entity:
                tracked.add(str(entity))
            for source in person.get("sources", []):
                tracked.add(str(source))

        anon = options.get(OPT_PEOPLE_ANON, {})
        for source in anon.get("sources", []):
            tracked.add(str(source))

        for room in options.get(OPT_ROOMS, []):
            for source in room.get("sources", []):
                tracked.add(str(source))

        security = options.get(OPT_SECURITY, {})
        security_entity = security.get("security_state_entity")
        if security_entity:
            tracked.add(str(security_entity))

        return tracked

    def _build_default_state(self) -> None:
        registry = build_registry(self._entry)
        self._state.binary_sensors = {desc.key: False for desc in registry.binary_sensors}
        self._state.sensors = {desc.key: None for desc in registry.sensors}
        self._state.selects = {
            desc.key: self._state.selects.get(desc.key, desc.options[0]) for desc in registry.selects
        }

        if "heima_people_count" in self._state.sensors:
            self._state.sensors["heima_people_count"] = 0
        if "heima_people_home_list" in self._state.sensors:
            self._state.sensors["heima_people_home_list"] = ""
        if "heima_house_state" in self._state.sensors:
            self._state.sensors["heima_house_state"] = "unknown"
        if "heima_house_state_reason" in self._state.sensors:
            self._state.sensors["heima_house_state_reason"] = ""
        if "heima_last_event" in self._state.sensors:
            self._state.sensors["heima_last_event"] = ""
        if "heima_event_stats" in self._state.sensors:
            self._state.sensors["heima_event_stats"] = "{}"

    def _compute_snapshot(self, reason: str) -> DecisionSnapshot:
        options = dict(self._entry.options)
        now = datetime.now(timezone.utc).isoformat()

        named_people = options.get(OPT_PEOPLE_NAMED, [])
        home_people: list[str] = []

        for person in named_people:
            slug = person.get("slug")
            if not slug:
                continue
            is_home, source, confidence = self._compute_named_person_presence(person)
            self._state.set_binary(f"heima_person_{slug}_home", is_home)
            self._state.set_sensor(f"heima_person_{slug}_source", source)
            self._state.set_sensor(f"heima_person_{slug}_confidence", confidence)
            if is_home:
                home_people.append(slug)

        anon_cfg = options.get(OPT_PEOPLE_ANON, {})
        anon_home = False
        anon_confidence = 0
        anon_source = "disabled"
        anon_weight = 0

        if anon_cfg.get("enabled"):
            anon_sources = list(anon_cfg.get("sources", []))
            required = int(anon_cfg.get("required", 1))
            anon_home, active_count = self._compute_group_presence(anon_sources, required)
            anon_confidence = 100 if anon_home else 0
            anon_source = ",".join(anon_sources) if anon_sources else "none"
            anon_weight = int(anon_cfg.get("anonymous_count_weight", 1)) if anon_home else 0
            self._state.set_binary("heima_anonymous_presence", anon_home)
            self._state.set_sensor("heima_anonymous_presence_confidence", anon_confidence)
            self._state.set_sensor("heima_anonymous_presence_source", anon_source)
            _LOGGER.debug("Anonymous presence active_count=%s", active_count)

        anyone_home = bool(home_people) or anon_home
        people_count = len(home_people) + anon_weight
        people_home_list = home_people + (["anonymous"] if anon_home else [])

        occupied_rooms: list[str] = []
        for room in options.get(OPT_ROOMS, []):
            room_id = room.get("room_id")
            if not room_id:
                continue
            is_occupied = self._compute_room_occupancy(room)
            prev_value = self._state.get_binary(f"heima_occ_{room_id}")
            self._state.set_binary(f"heima_occ_{room_id}", is_occupied)
            self._state.set_sensor(
                f"heima_occ_{room_id}_source",
                ",".join(room.get("sources", [])),
            )
            if prev_value != is_occupied:
                self._state.set_sensor(f"heima_occ_{room_id}_last_change", now)
            if is_occupied:
                occupied_rooms.append(room_id)

        security_cfg = options.get(OPT_SECURITY, {})
        security_state = "unknown"
        security_reason = "disabled"
        if security_cfg.get("enabled"):
            security_state = self._read_state(str(security_cfg.get("security_state_entity", ""))) or "unknown"
            security_reason = "bound_entity"
            self._state.set_sensor("heima_security_state", security_state)
            self._state.set_sensor("heima_security_reason", security_reason)

        vacation_mode = self._is_on_any(["input_boolean.vacation_mode"])
        guest_mode = self._is_on_any(["input_boolean.guest_mode"])
        sleep_window = self._is_on_any(["binary_sensor.sleep_window"])
        relax_mode = self._is_on_any(["binary_sensor.relax_mode"])
        work_window = self._is_on_any(["binary_sensor.work_window"])

        house_state, house_reason = resolve_house_state(
            anyone_home=anyone_home,
            vacation_mode=vacation_mode,
            guest_mode=guest_mode,
            sleep_window=sleep_window,
            relax_mode=relax_mode,
            work_window=work_window,
        )

        lighting_intents = self._compute_lighting_intents(
            house_state=house_state,
            occupied_rooms=occupied_rooms,
        )

        heating_intent = self._state.get_select("heima_heating_intent") or "auto"

        self._state.set_binary("heima_anyone_home", anyone_home)
        self._state.set_sensor("heima_people_count", people_count)
        self._state.set_sensor("heima_people_home_list", ",".join(people_home_list))
        self._state.set_sensor("heima_house_state", house_state)
        self._state.set_sensor("heima_house_state_reason", house_reason)

        if security_reason == "disabled" and "heima_security_reason" in self._state.sensors:
            self._state.set_sensor("heima_security_reason", security_reason)

        return DecisionSnapshot(
            snapshot_id=str(uuid4()),
            ts=now,
            house_state=house_state,
            anyone_home=anyone_home,
            people_count=people_count,
            occupied_rooms=occupied_rooms,
            lighting_intents=lighting_intents,
            heating_intent=heating_intent,
            security_state=security_state,
            notes=f"reason={reason}",
        )

    def _compute_lighting_intents(self, house_state: str, occupied_rooms: list[str]) -> dict[str, str]:
        options = dict(self._entry.options)
        occupied = set(occupied_rooms)
        lighting_intents: dict[str, str] = {}

        for zone in options.get(OPT_LIGHTING_ZONES, []):
            zone_id = zone.get("zone_id")
            if not zone_id:
                continue
            rooms = list(zone.get("rooms", []))
            zone_occupied = any(room_id in occupied for room_id in rooms)

            select_key = f"heima_lighting_intent_{zone_id}"
            requested_intent = self._state.get_select(select_key) or "auto"
            final_intent = resolve_zone_intent(requested_intent, house_state, zone_occupied)
            lighting_intents[zone_id] = final_intent

        return lighting_intents

    def _build_apply_plan(self, snapshot: DecisionSnapshot) -> ApplyPlan:
        room_maps = self._lighting_room_maps()
        steps: list[ApplyStep] = []

        for zone_id, intent in snapshot.lighting_intents.items():
            for room_id in self._zone_rooms(zone_id):
                if self._is_lighting_room_hold_on(room_id):
                    continue

                room_map = room_maps.get(room_id)
                if not room_map:
                    continue

                scene_entity = pick_scene_for_intent(room_map, intent)
                if not scene_entity:
                    continue

                if not self._should_apply_scene(room_id, scene_entity):
                    continue

                steps.append(
                    ApplyStep(
                        domain="lighting",
                        target=room_id,
                        action="scene.turn_on",
                        params={"entity_id": scene_entity},
                        reason=f"intent:{intent}",
                    )
                )

        return ApplyPlan(steps=steps)

    async def _execute_apply_plan(self, plan: ApplyPlan) -> None:
        for step in plan.steps:
            if step.action != "scene.turn_on":
                continue

            scene_entity = step.params.get("entity_id")
            if not isinstance(scene_entity, str) or not scene_entity.startswith("scene."):
                continue

            if self._hass.states.get(scene_entity) is None:
                _LOGGER.warning("Skipping missing scene entity: %s", scene_entity)
                continue

            await self._hass.services.async_call(
                "scene",
                "turn_on",
                {"entity_id": scene_entity},
                blocking=False,
            )
            self._mark_scene_applied(step.target, scene_entity)

    def _should_apply_scene(self, room_id: str, scene_entity: str) -> bool:
        now = time.monotonic()
        last_scene = self._lighting_last_scene.get(room_id)
        last_ts = self._lighting_last_ts.get(room_id, 0.0)

        if last_scene == scene_entity and (now - last_ts) < _LIGHTING_MIN_SECONDS_BETWEEN_APPLIES:
            return False

        return True

    def _mark_scene_applied(self, room_id: str, scene_entity: str) -> None:
        self._lighting_last_scene[room_id] = scene_entity
        self._lighting_last_ts[room_id] = time.monotonic()

    def _lighting_room_maps(self) -> dict[str, dict[str, Any]]:
        options = dict(self._entry.options)
        mappings: dict[str, dict[str, Any]] = {}
        for room_map in options.get(OPT_LIGHTING_ROOMS, []):
            room_id = room_map.get("room_id")
            if room_id:
                mappings[str(room_id)] = dict(room_map)
        return mappings

    def _lighting_apply_mode(self) -> str:
        mode = str(
            dict(self._entry.options).get(OPT_LIGHTING_APPLY_MODE, DEFAULT_LIGHTING_APPLY_MODE)
        )
        if mode not in {"scene", "delegate"}:
            return DEFAULT_LIGHTING_APPLY_MODE
        return mode

    def _is_lighting_room_hold_on(self, room_id: str) -> bool:
        key = f"heima_lighting_manual_hold_{room_id}"
        value = self._state.get_binary(key)
        return bool(value)

    def _compute_named_person_presence(self, person_cfg: dict[str, Any]) -> tuple[bool, str, int]:
        method = person_cfg.get("presence_method", "ha_person")
        if method == "ha_person":
            entity_id = person_cfg.get("person_entity")
            is_home = self._is_entity_home(entity_id)
            return is_home, "ha_person", 100 if is_home else 0

        if method == "quorum":
            sources = list(person_cfg.get("sources", []))
            required = int(person_cfg.get("required", 1))
            is_home, active_count = self._compute_group_presence(sources, required)
            confidence = int((active_count / max(1, len(sources))) * 100) if sources else 0
            return is_home, "quorum", confidence

        slug = str(person_cfg.get("slug", ""))
        override = self._state.get_select(f"heima_person_{slug}_override")
        if override == "force_home":
            return True, "manual", 100
        if override == "force_away":
            return False, "manual", 100
        return False, "manual", 0

    def _compute_group_presence(self, sources: list[str], required: int) -> tuple[bool, int]:
        active = 0
        for entity_id in sources:
            if self._is_presence_on(entity_id):
                active += 1
        return active >= max(1, required), active

    def _compute_room_occupancy(self, room_cfg: dict[str, Any]) -> bool:
        sources = list(room_cfg.get("sources", []))
        logic = room_cfg.get("logic", "any_of")
        if not sources:
            return False

        values = [self._is_presence_on(entity_id) for entity_id in sources]
        return all(values) if logic == "all_of" else any(values)

    def _is_entity_home(self, entity_id: str | None) -> bool:
        if not entity_id:
            return False
        state = self._read_state(entity_id)
        return state == "home"

    def _is_presence_on(self, entity_id: str | None) -> bool:
        state = self._read_state(entity_id)
        if state is None:
            return False
        lowered = state.lower()
        if lowered in _PRESENCE_ON_STATES:
            return True
        try:
            return float(state) > 0
        except ValueError:
            return False

    def _is_on_any(self, entity_ids: list[str]) -> bool:
        return any(self._is_presence_on(entity_id) for entity_id in entity_ids)

    def _read_state(self, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        state = self._hass.states.get(entity_id)
        return state.state if state else None

    def _apply_snapshot_to_canonical_state(self, snapshot: DecisionSnapshot) -> None:
        for zone_id in list(snapshot.lighting_intents.keys()):
            key = f"heima_occ_zone_{zone_id}"
            zone_rooms = self._zone_rooms(zone_id)
            zone_is_on = any(room in snapshot.occupied_rooms for room in zone_rooms)
            if key in self._state.binary_sensors:
                self._state.set_binary(key, zone_is_on)

    def _zone_rooms(self, zone_id: str) -> list[str]:
        options = dict(self._entry.options)
        for zone in options.get(OPT_LIGHTING_ZONES, []):
            if zone.get("zone_id") == zone_id:
                return list(zone.get("rooms", []))
        return []
