"""Heima runtime engine (core + lighting v1)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound

from ..const import (
    DEFAULT_LIGHTING_APPLY_MODE,
    DEFAULT_ENABLED_EVENT_CATEGORIES,
    DEFAULT_OCCUPANCY_MISMATCH_MIN_DERIVED_ROOMS,
    DEFAULT_OCCUPANCY_MISMATCH_PERSIST_S,
    DEFAULT_OCCUPANCY_MISMATCH_POLICY,
    DEFAULT_SECURITY_MISMATCH_PERSIST_S,
    DEFAULT_SECURITY_MISMATCH_POLICY,
    EVENT_CATEGORIES_ALL,
    OPT_LIGHTING_APPLY_MODE,
    OPT_LIGHTING_ROOMS,
    OPT_LIGHTING_ZONES,
    OPT_NOTIFICATIONS,
    OPT_PEOPLE_ANON,
    OPT_PEOPLE_NAMED,
    OPT_ROOMS,
    OPT_SECURITY,
)
from ..entities.registry import build_registry
from ..models import HeimaOptions
from .contracts import ApplyPlan, ApplyStep, HeimaEvent
from .lighting import pick_scene_for_intent_with_trace, resolve_zone_intent
from .normalization.service import InputNormalizer
from .notifications import HeimaEventPipeline
from .policy import resolve_house_state
from .snapshot import DecisionSnapshot
from .state_store import CanonicalState

_LOGGER = logging.getLogger(__name__)

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
        self._lighting_hold_seen_state: dict[str, bool] = {}
        self._lighting_zone_trace: dict[str, dict[str, Any]] = {}
        self._lighting_room_trace: dict[str, list[dict[str, Any]]] = {}
        self._lighting_conflicts_last_eval: list[dict[str, Any]] = []
        self._last_engine_enabled_state: bool | None = None
        self._events = HeimaEventPipeline(hass)
        self._normalizer = InputNormalizer(hass)
        self._pending_events: list[HeimaEvent] = []
        self._suppressed_event_categories: dict[str, int] = {}
        self._occupancy_home_no_room_since: float | None = None
        self._occupancy_home_no_room_emitted: bool = False
        self._occupancy_room_no_home_since: dict[str, float] = {}
        self._occupancy_room_no_home_emitted: set[str] = set()
        self._occupancy_room_candidate_state: dict[str, str] = {}
        self._occupancy_room_candidate_since: dict[str, float] = {}
        self._occupancy_room_effective_state: dict[str, str] = {}
        self._occupancy_room_effective_since: dict[str, float] = {}
        self._occupancy_room_trace: dict[str, dict[str, Any]] = {}
        self._security_observation_trace: dict[str, Any] = {}
        self._security_armed_away_but_home_since: float | None = None
        self._security_armed_away_but_home_emitted: bool = False

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
        await self._emit_lighting_hold_events()
        await self._emit_queued_events()

        if self._last_engine_enabled_state is None or self._last_engine_enabled_state != self._options.engine_enabled:
            if not self._options.engine_enabled:
                await self._emit_event_obj(
                    HeimaEvent(
                        type="system.engine_disabled",
                        key="system.engine_disabled",
                        severity="info",
                        title="Heima engine disabled",
                        message="Heima engine apply phases are disabled; canonical state continues updating.",
                        context={"reason": "engine_enabled_false"},
                    )
                )
                self._sync_event_sensors()
            self._last_engine_enabled_state = self._options.engine_enabled

        if self._options.engine_enabled and self._lighting_apply_mode() == "scene":
            await self._execute_apply_plan(plan)

        return snapshot

    async def async_emit_external_event(
        self,
        *,
        event_type: str,
        key: str,
        severity: str,
        title: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Emit an external/runtime event through the unified event pipeline."""
        emitted = await self._emit_event_obj(
            HeimaEvent(
                type=event_type,
                key=key,
                severity=severity,
                title=title,
                message=message,
                context=dict(context or {}),
            )
        )
        self._sync_event_sensors()
        return emitted

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
            prev_is_home = self._state.get_binary(f"heima_person_{slug}_home")
            self._state.set_binary(f"heima_person_{slug}_home", is_home)
            self._state.set_sensor(f"heima_person_{slug}_source", source)
            self._state.set_sensor(f"heima_person_{slug}_confidence", confidence)
            self._queue_people_transition_event(
                slug=slug,
                prev_is_home=prev_is_home,
                is_home=is_home,
                source=source,
                confidence=confidence,
            )
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
            prev_anon_home = self._state.get_binary("heima_anonymous_presence")
            self._state.set_binary("heima_anonymous_presence", anon_home)
            self._state.set_sensor("heima_anonymous_presence_confidence", anon_confidence)
            self._state.set_sensor("heima_anonymous_presence_source", anon_source)
            self._queue_anonymous_transition_event(
                prev_is_on=prev_anon_home,
                is_on=anon_home,
                source=anon_source,
                confidence=anon_confidence,
                weight=int(anon_cfg.get("anonymous_count_weight", 1)),
            )
            _LOGGER.debug("Anonymous presence active_count=%s", active_count)

        anyone_home = bool(home_people) or anon_home
        people_count = len(home_people) + anon_weight
        people_home_list = home_people + (["anonymous"] if anon_home else [])

        occupied_rooms: list[str] = []
        for room in options.get(OPT_ROOMS, []):
            room_id = room.get("room_id")
            if not room_id:
                continue
            is_occupied, occ_trace = self._compute_room_occupancy(room)
            prev_value = self._state.get_binary(f"heima_occ_{room_id}")
            self._state.set_binary(f"heima_occ_{room_id}", is_occupied)
            self._state.set_sensor(
                f"heima_occ_{room_id}_source",
                "none" if self._room_occupancy_mode(room) == "none" else ",".join(room.get("sources", [])),
            )
            if prev_value != is_occupied:
                self._state.set_sensor(f"heima_occ_{room_id}_last_change", now)
            self._occupancy_room_trace[str(room_id)] = occ_trace
            if is_occupied:
                occupied_rooms.append(room_id)

        security_cfg = options.get(OPT_SECURITY, {})
        security_state = "unknown"
        security_reason = "disabled"
        if security_cfg.get("enabled"):
            entity_id = str(security_cfg.get("security_state_entity", ""))
            security_obs = self._normalizer.security(
                entity_id,
                {
                    "armed_away_value": security_cfg.get("armed_away_value", "armed_away"),
                    "armed_home_value": security_cfg.get("armed_home_value", "armed_home"),
                },
            )
            security_state = security_obs.state
            security_reason = security_obs.reason or "normalized"
            self._security_observation_trace = security_obs.as_dict()
            self._state.set_sensor("heima_security_state", security_state)
            self._state.set_sensor("heima_security_reason", security_reason)
        else:
            self._security_observation_trace = {
                "state": "unknown",
                "reason": "disabled",
                "available": False,
                "source_entity_id": None,
            }

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
        prev_house_state = self._state.get_sensor("heima_house_state")
        self._state.set_sensor("heima_house_state", house_state)
        self._state.set_sensor("heima_house_state_reason", house_reason)
        self._queue_house_state_changed_event(
            previous=str(prev_house_state) if prev_house_state not in (None, "") else None,
            current=house_state,
            reason=house_reason,
        )

        if security_reason == "disabled" and "heima_security_reason" in self._state.sensors:
            self._state.set_sensor("heima_security_reason", security_reason)

        self._queue_occupancy_consistency_events(
            anyone_home=anyone_home,
            occupied_rooms=occupied_rooms,
            options=options,
        )
        self._queue_security_consistency_events(
            anyone_home=anyone_home,
            security_state=security_state,
            options=options,
            people_home_list=people_home_list,
            occupied_rooms=occupied_rooms,
        )

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
        room_configs = self._room_configs()
        lighting_intents: dict[str, str] = {}
        zone_trace: dict[str, dict[str, Any]] = {}

        for zone in options.get(OPT_LIGHTING_ZONES, []):
            zone_id = zone.get("zone_id")
            if not zone_id:
                continue
            rooms = list(zone.get("rooms", []))
            occupancy_capable_rooms = [
                room_id
                for room_id in rooms
                if self._room_occupancy_mode(room_configs.get(room_id, {})) == "derived"
            ]
            zone_occupied = any(room_id in occupied for room_id in occupancy_capable_rooms)

            select_key = f"heima_lighting_intent_{zone_id}"
            requested_intent = self._state.get_select(select_key) or "auto"
            final_intent = resolve_zone_intent(requested_intent, house_state, zone_occupied)
            lighting_intents[zone_id] = final_intent
            zone_trace[str(zone_id)] = {
                "zone_id": str(zone_id),
                "rooms": rooms,
                "occupancy_capable_rooms": occupancy_capable_rooms,
                "zone_occupied": zone_occupied,
                "requested_intent": requested_intent,
                "final_intent": final_intent,
                "house_state": house_state,
            }

        self._lighting_zone_trace = zone_trace
        return lighting_intents

    def _build_apply_plan(self, snapshot: DecisionSnapshot) -> ApplyPlan:
        room_maps = self._lighting_room_maps()
        room_configs = self._room_configs()
        steps: list[ApplyStep] = []
        room_trace: dict[str, list[dict[str, Any]]] = {}
        room_winner_by_room: dict[str, dict[str, Any]] = {}
        conflicts: list[dict[str, Any]] = []

        def _enqueue_lighting_step(
            *,
            room_id: str,
            zone_id: str,
            intent: str,
            action: str,
            action_params: dict[str, Any],
            scene_entity: str | None,
            decision: dict[str, Any],
            reason: str,
        ) -> bool:
            winner = room_winner_by_room.get(room_id)
            if winner is not None:
                conflict = {
                    "room_id": room_id,
                    "policy": "first_wins",
                    "winning_zone": winner["zone_id"],
                    "winning_intent": winner["intent"],
                    "winning_scene": winner.get("scene_entity"),
                    "winning_action": winner["action"],
                    "dropped_zone": zone_id,
                    "dropped_intent": intent,
                    "dropped_scene": scene_entity,
                    "dropped_action": action,
                }
                conflicts.append(conflict)
                decision["skip_reason"] = "zone_conflict_dropped"
                decision["conflict"] = dict(conflict)
                room_trace.setdefault(room_id, []).append(decision)
                self._queue_event(
                    HeimaEvent(
                        type="lighting.zone_conflict",
                        key=f"lighting.zone_conflict.{room_id}",
                        severity="warn",
                        title="Lighting zone conflict",
                        message=(
                            f"Multiple lighting zones targeted room '{room_id}' "
                            f"in the same evaluation; first valid step kept."
                        ),
                        context={
                            "room": room_id,
                            "winning_zone": winner["zone_id"],
                            "winning_intent": winner["intent"],
                            "winning_scene": winner.get("scene_entity"),
                            "dropped_zone": zone_id,
                            "dropped_intent": intent,
                            "dropped_scene": scene_entity,
                            "policy": "first_wins",
                        },
                    )
                )
                _LOGGER.warning(
                    "Lighting zone conflict for room '%s': keeping first valid step from zone=%s intent=%s; dropping zone=%s intent=%s",
                    room_id,
                    winner["zone_id"],
                    winner["intent"],
                    zone_id,
                    intent,
                )
                return False

            decision["apply_queued"] = True
            room_trace.setdefault(room_id, []).append(decision)
            room_winner_by_room[room_id] = {
                "zone_id": zone_id,
                "intent": intent,
                "scene_entity": scene_entity,
                "action": action,
                "params": dict(action_params),
            }
            steps.append(
                ApplyStep(
                    domain="lighting",
                    target=room_id,
                    action=action,
                    params=dict(action_params),
                    reason=reason,
                )
            )
            return True

        for zone_id, intent in snapshot.lighting_intents.items():
            for room_id in self._zone_rooms(zone_id):
                decision: dict[str, Any] = {
                    "zone_id": zone_id,
                    "room_id": room_id,
                    "intent": intent,
                    "hold": False,
                    "room_occupancy_mode": self._room_occupancy_mode(room_configs.get(room_id, {})),
                    "contributes_to_zone_occupancy": (
                        self._room_occupancy_mode(room_configs.get(room_id, {})) == "derived"
                    ),
                    "room_mapping_found": False,
                    "action": None,
                    "action_params": None,
                    "scene_entity": None,
                    "scene_resolution": None,
                    "apply_queued": False,
                    "skip_reason": None,
                }
                if self._is_lighting_room_hold_on(room_id):
                    decision["hold"] = True
                    decision["skip_reason"] = "manual_hold"
                    room_trace.setdefault(room_id, []).append(decision)
                    continue

                room_map = room_maps.get(room_id)
                if not room_map:
                    decision["skip_reason"] = "no_room_mapping"
                    room_trace.setdefault(room_id, []).append(decision)
                    continue
                decision["room_mapping_found"] = True

                scene_entity, scene_resolution = pick_scene_for_intent_with_trace(room_map, intent)
                decision["scene_entity"] = scene_entity
                decision["scene_resolution"] = scene_resolution
                if not scene_entity:
                    if intent == "off":
                        area_id = str(room_configs.get(room_id, {}).get("area_id") or "").strip()
                        if area_id:
                            action_fingerprint = f"light.turn_off:area:{area_id}"
                            if not self._should_apply_scene(room_id, action_fingerprint):
                                decision["skip_reason"] = "rate_limited_or_duplicate"
                                decision["scene_resolution"] = "fallback:off->light.turn_off(area)"
                                decision["action"] = "light.turn_off"
                                decision["action_params"] = {"area_id": area_id}
                                room_trace.setdefault(room_id, []).append(decision)
                                continue

                            decision["scene_resolution"] = "fallback:off->light.turn_off(area)"
                            decision["action"] = "light.turn_off"
                            decision["action_params"] = {"area_id": area_id}
                            _enqueue_lighting_step(
                                room_id=room_id,
                                zone_id=zone_id,
                                intent=intent,
                                action="light.turn_off",
                                action_params={"area_id": area_id},
                                scene_entity=None,
                                decision=decision,
                                reason="intent:off(area_fallback)",
                            )
                            continue

                    decision["skip_reason"] = "scene_missing"
                    room_trace.setdefault(room_id, []).append(decision)
                    self._queue_event(
                        HeimaEvent(
                            type="lighting.scene_missing",
                            key=f"lighting.scene_missing.{room_id}.{intent}",
                            severity="warn",
                            title="Lighting scene missing",
                            message=(
                                f"No mapped scene for room '{room_id}' "
                                f"and intent '{intent}'"
                            ),
                            context={"room": room_id, "intent": intent, "expected_scene": intent},
                        )
                    )
                    continue

                if not self._should_apply_scene(room_id, scene_entity):
                    decision["skip_reason"] = "rate_limited_or_duplicate"
                    room_trace.setdefault(room_id, []).append(decision)
                    continue

                _enqueue_lighting_step(
                    room_id=room_id,
                    zone_id=zone_id,
                    intent=intent,
                    action="scene.turn_on",
                    action_params={"entity_id": scene_entity},
                    scene_entity=scene_entity,
                    decision=decision,
                    reason=f"intent:{intent}",
                )

        self._lighting_room_trace = room_trace
        self._lighting_conflicts_last_eval = conflicts
        return ApplyPlan(steps=steps)

    async def _execute_apply_plan(self, plan: ApplyPlan) -> None:
        for step in plan.steps:
            if step.action == "scene.turn_on":
                scene_entity = step.params.get("entity_id")
                if not isinstance(scene_entity, str) or not scene_entity.startswith("scene."):
                    continue

                if self._hass.states.get(scene_entity) is None:
                    _LOGGER.warning("Skipping missing scene entity: %s", scene_entity)
                    continue
                try:
                    await self._hass.services.async_call(
                        "scene",
                        "turn_on",
                        {"entity_id": scene_entity},
                        blocking=False,
                    )
                    self._mark_scene_applied(step.target, scene_entity)
                    continue
                except ServiceNotFound:
                    _LOGGER.warning(
                        "Skipping lighting apply during startup/race: service scene.turn_on not available"
                    )
                    continue
                except Exception:
                    _LOGGER.exception("Lighting apply failed for scene '%s'", scene_entity)
                    continue

            if step.action == "light.turn_off":
                area_id = step.params.get("area_id")
                if not isinstance(area_id, str) or not area_id:
                    continue
                try:
                    await self._hass.services.async_call(
                        "light",
                        "turn_off",
                        {"area_id": area_id},
                        blocking=False,
                    )
                    self._mark_scene_applied(step.target, f"light.turn_off:area:{area_id}")
                    continue
                except ServiceNotFound:
                    _LOGGER.warning(
                        "Skipping lighting apply during startup/race: service light.turn_off not available"
                    )
                    continue
                except Exception:
                    _LOGGER.exception("Lighting apply failed for room area '%s'", area_id)
                    continue

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

    def _room_configs(self) -> dict[str, dict[str, Any]]:
        options = dict(self._entry.options)
        configs: dict[str, dict[str, Any]] = {}
        for room in options.get(OPT_ROOMS, []):
            room_id = room.get("room_id")
            if room_id:
                configs[str(room_id)] = dict(room)
        return configs

    def _queue_event(self, event: HeimaEvent) -> None:
        self._pending_events.append(event)

    def _queue_people_transition_event(
        self,
        *,
        slug: str,
        prev_is_home: bool | None,
        is_home: bool,
        source: str,
        confidence: int,
    ) -> None:
        if prev_is_home is None or prev_is_home == is_home:
            return
        self._queue_event(
            HeimaEvent(
                type="people.arrive" if is_home else "people.leave",
                key=f"{'people.arrive' if is_home else 'people.leave'}.{slug}",
                severity="info",
                title="Person arrived" if is_home else "Person left",
                message=f"Person '{slug}' {'arrived' if is_home else 'left'}.",
                context={"person": slug, "source": source, "confidence": confidence},
            )
        )

    def _queue_anonymous_transition_event(
        self,
        *,
        prev_is_on: bool | None,
        is_on: bool,
        source: str,
        confidence: int,
        weight: int,
    ) -> None:
        if prev_is_on is None or prev_is_on == is_on:
            return
        context: dict[str, Any] = {"source": source, "confidence": confidence}
        if is_on:
            context["weight"] = weight
        self._queue_event(
            HeimaEvent(
                type="people.anonymous_on" if is_on else "people.anonymous_off",
                key="people.anonymous",
                severity="info",
                title="Anonymous presence detected" if is_on else "Anonymous presence cleared",
                message=(
                    "Anonymous presence detected."
                    if is_on
                    else "Anonymous presence cleared."
                ),
                context=context,
            )
        )

    def _queue_house_state_changed_event(
        self, *, previous: str | None, current: str, reason: str
    ) -> None:
        if previous is None or previous == "unknown" or previous == current:
            return
        self._queue_event(
            HeimaEvent(
                type="house_state.changed",
                key="house_state.changed",
                severity="info",
                title="House state changed",
                message=f"House state changed from '{previous}' to '{current}'.",
                context={"from": previous, "to": current, "reason": reason},
            )
        )

    def _queue_occupancy_consistency_events(
        self, *, anyone_home: bool, occupied_rooms: list[str], options: dict[str, Any]
    ) -> None:
        mismatch_cfg = self._occupancy_mismatch_config()
        policy = mismatch_cfg["policy"]
        if policy == "off":
            self._occupancy_home_no_room_since = None
            self._occupancy_home_no_room_emitted = False
            self._occupancy_room_no_home_since.clear()
            self._occupancy_room_no_home_emitted.clear()
            return

        derived_rooms = [
            str(room.get("room_id"))
            for room in options.get(OPT_ROOMS, [])
            if room.get("room_id") and self._room_occupancy_mode(room) == "derived"
        ]
        derived_room_count = len(derived_rooms)
        persist_s = mismatch_cfg["persist_s"]
        min_derived_rooms = mismatch_cfg["min_derived_rooms"]

        home_no_room_condition = anyone_home and not occupied_rooms
        if policy == "smart" and derived_room_count < min_derived_rooms:
            home_no_room_condition = False

        if self._persistent_condition_ready(
            key="home_no_room",
            active=home_no_room_condition,
            persist_s=0 if policy == "strict" else persist_s,
        ):
            self._queue_event(
                HeimaEvent(
                    type="occupancy.inconsistency_home_no_room",
                    key="occupancy.inconsistency_home_no_room",
                    severity="info",
                    title="Occupancy inconsistency",
                    message="Someone is home but no room occupancy is active.",
                    context={
                        "anyone_home": anyone_home,
                        "occupied_rooms": list(occupied_rooms),
                        "policy": policy,
                        "derived_room_count": derived_room_count,
                        "persist_s": 0 if policy == "strict" else persist_s,
                    },
                )
            )

        room_sources = {
            str(room.get("room_id")): list(room.get("sources", []))
            for room in options.get(OPT_ROOMS, [])
            if room.get("room_id")
        }

        room_configs = self._room_configs()
        active_room_no_home = set()
        if occupied_rooms and not anyone_home:
            for room_id in occupied_rooms:
                room_cfg = room_configs.get(room_id, {})
                if self._room_occupancy_mode(room_cfg) != "derived":
                    self._reset_persistent_room_condition(room_id)
                    continue
                active_room_no_home.add(room_id)
                if not self._persistent_condition_ready(
                    key=f"room_no_home:{room_id}",
                    active=True,
                    persist_s=0 if policy == "strict" else persist_s,
                ):
                    continue
                self._queue_event(
                    HeimaEvent(
                        type="occupancy.inconsistency_room_no_home",
                        key=f"occupancy.inconsistency_room_no_home.{room_id}",
                        severity="info",
                        title="Occupancy inconsistency",
                        message=f"Room '{room_id}' is occupied but nobody is home.",
                        context={
                            "room": room_id,
                            "anyone_home": anyone_home,
                            "source_entities": room_sources.get(room_id, []),
                            "policy": policy,
                            "persist_s": 0 if policy == "strict" else persist_s,
                        },
                    )
                )

        for room_id in list(self._occupancy_room_no_home_since.keys()):
            if room_id not in active_room_no_home:
                self._reset_persistent_room_condition(room_id)

    def _queue_security_consistency_events(
        self,
        *,
        anyone_home: bool,
        security_state: str,
        options: dict[str, Any],
        people_home_list: list[str],
        occupied_rooms: list[str],
    ) -> None:
        security_cfg = dict(options.get(OPT_SECURITY, {}))
        if not security_cfg.get("enabled"):
            self._security_armed_away_but_home_since = None
            self._security_armed_away_but_home_emitted = False
            return

        mismatch_cfg = self._security_mismatch_config()
        policy = mismatch_cfg["policy"]
        if policy == "off":
            self._security_armed_away_but_home_since = None
            self._security_armed_away_but_home_emitted = False
            return

        mismatch_active = security_state == "armed_away" and anyone_home
        persist_s = 0 if policy == "strict" else mismatch_cfg["persist_s"]

        room_configs = self._room_configs()
        has_room_evidence = any(
            self._room_occupancy_mode(room_configs.get(room_id, {})) == "derived"
            for room_id in occupied_rooms
        )
        has_anonymous_evidence = bool(self._state.get_binary("heima_anonymous_presence"))
        if policy == "smart":
            mismatch_active = mismatch_active and (has_room_evidence or has_anonymous_evidence)

        if self._persistent_security_mismatch_ready(active=mismatch_active, persist_s=persist_s):
            self._queue_event(
                HeimaEvent(
                    type="security.armed_away_but_home",
                    key="security.armed_away_but_home",
                    severity="warn",
                    title="Security inconsistency",
                    message="Security is armed away while someone is home.",
                    context={
                        "security_state": security_state,
                        "security_observation_reason": self._security_observation_trace.get("reason"),
                        "people_home_list": list(people_home_list),
                        "policy": policy,
                        "persist_s": persist_s,
                        "occupied_rooms": list(occupied_rooms),
                        "has_room_evidence": has_room_evidence,
                        "has_anonymous_evidence": has_anonymous_evidence,
                    },
                )
            )

    async def _emit_queued_events(self) -> None:
        if not self._pending_events:
            self._sync_event_sensors()
            return

        queued = list(self._pending_events)
        self._pending_events.clear()
        for event in queued:
            await self._emit_event_obj(event)

        self._sync_event_sensors()

    async def _emit_event_obj(self, event: HeimaEvent) -> bool:
        if not self._event_enabled(event):
            category = self._event_category(event.type)
            self._suppressed_event_categories[category] = (
                self._suppressed_event_categories.get(category, 0) + 1
            )
            _LOGGER.debug("Heima event suppressed by category toggle: %s (%s)", event.type, category)
            return False
        notifications_cfg = self._notifications_config()
        return await self._events.async_emit(
            event,
            routes=list(notifications_cfg.get("routes", [])),
            dedup_window_s=int(notifications_cfg.get("dedup_window_s", 60)),
            rate_limit_per_key_s=int(notifications_cfg.get("rate_limit_per_key_s", 300)),
        )

    def _notifications_config(self) -> dict[str, Any]:
        return dict(dict(self._entry.options).get(OPT_NOTIFICATIONS, {}))

    def _occupancy_mismatch_config(self) -> dict[str, Any]:
        cfg = self._notifications_config()
        policy = str(cfg.get("occupancy_mismatch_policy", DEFAULT_OCCUPANCY_MISMATCH_POLICY))
        if policy not in {"off", "smart", "strict"}:
            policy = DEFAULT_OCCUPANCY_MISMATCH_POLICY
        return {
            "policy": policy,
            "min_derived_rooms": int(
                cfg.get(
                    "occupancy_mismatch_min_derived_rooms",
                    DEFAULT_OCCUPANCY_MISMATCH_MIN_DERIVED_ROOMS,
                )
            ),
            "persist_s": int(
                cfg.get("occupancy_mismatch_persist_s", DEFAULT_OCCUPANCY_MISMATCH_PERSIST_S)
            ),
        }

    def _security_mismatch_config(self) -> dict[str, Any]:
        cfg = self._notifications_config()
        policy = str(cfg.get("security_mismatch_policy", DEFAULT_SECURITY_MISMATCH_POLICY))
        if policy not in {"off", "smart", "strict"}:
            policy = DEFAULT_SECURITY_MISMATCH_POLICY
        return {
            "policy": policy,
            "persist_s": int(cfg.get("security_mismatch_persist_s", DEFAULT_SECURITY_MISMATCH_PERSIST_S)),
        }

    def _persistent_condition_ready(self, *, key: str, active: bool, persist_s: int) -> bool:
        now = time.monotonic()
        if key == "home_no_room":
            if not active:
                self._occupancy_home_no_room_since = None
                self._occupancy_home_no_room_emitted = False
                return False
            if self._occupancy_home_no_room_since is None:
                self._occupancy_home_no_room_since = now
                self._occupancy_home_no_room_emitted = False
            if self._occupancy_home_no_room_emitted:
                return False
            if persist_s <= 0 or (now - self._occupancy_home_no_room_since) >= persist_s:
                self._occupancy_home_no_room_emitted = True
                return True
            return False

        if key.startswith("room_no_home:"):
            room_id = key.split(":", 1)[1]
            if not active:
                self._reset_persistent_room_condition(room_id)
                return False
            if room_id not in self._occupancy_room_no_home_since:
                self._occupancy_room_no_home_since[room_id] = now
                self._occupancy_room_no_home_emitted.discard(room_id)
            if room_id in self._occupancy_room_no_home_emitted:
                return False
            if persist_s <= 0 or (now - self._occupancy_room_no_home_since[room_id]) >= persist_s:
                self._occupancy_room_no_home_emitted.add(room_id)
                return True
            return False

        return False

    def _reset_persistent_room_condition(self, room_id: str) -> None:
        self._occupancy_room_no_home_since.pop(room_id, None)
        self._occupancy_room_no_home_emitted.discard(room_id)

    def _persistent_security_mismatch_ready(self, *, active: bool, persist_s: int) -> bool:
        now = time.monotonic()
        if not active:
            self._security_armed_away_but_home_since = None
            self._security_armed_away_but_home_emitted = False
            return False
        if self._security_armed_away_but_home_since is None:
            self._security_armed_away_but_home_since = now
            self._security_armed_away_but_home_emitted = False
        if self._security_armed_away_but_home_emitted:
            return False
        if persist_s <= 0 or (now - self._security_armed_away_but_home_since) >= persist_s:
            self._security_armed_away_but_home_emitted = True
            return True
        return False

    def _event_category(self, event_type: str) -> str:
        prefix = str(event_type or "").split(".", 1)[0]
        return prefix or "system"

    def _enabled_event_categories(self) -> set[str]:
        cfg = self._notifications_config()
        raw = cfg.get("enabled_event_categories")
        if raw is None:
            return set(DEFAULT_ENABLED_EVENT_CATEGORIES) | {"system"}
        enabled = {str(v) for v in list(raw) if str(v)}
        enabled.add("system")  # system is always enabled by spec
        return enabled

    def _event_enabled(self, event: HeimaEvent) -> bool:
        category = self._event_category(event.type)
        if category == "system":
            return True
        # Unknown/custom categories (e.g. debug.manual_test) stay enabled unless explicitly standardized.
        known_categories = set(EVENT_CATEGORIES_ALL)
        if category not in known_categories:
            return True
        return category in self._enabled_event_categories()

    def _sync_event_sensors(self) -> None:
        stats = self._events.stats.as_dict()
        if "heima_last_event" in self._state.sensors:
            last_event = stats.get("last_event") or {}
            self._state.set_sensor("heima_last_event", str(last_event.get("type", "")))
        if "heima_event_stats" in self._state.sensors:
            last_event = stats.get("last_event") or {}
            summary = (
                f"emitted={stats.get('emitted', 0)} "
                f"dedup={stats.get('dropped_dedup', 0)} "
                f"rate={stats.get('dropped_rate_limited', 0)} "
                f"last={last_event.get('type', '')}"
            ).strip()
            self._state.set_sensor("heima_event_stats", summary[:255])
            self._state.set_sensor_attributes(
                "heima_event_stats",
                {
                    "emitted": stats.get("emitted", 0),
                    "dropped_dedup": stats.get("dropped_dedup", 0),
                    "dropped_rate_limited": stats.get("dropped_rate_limited", 0),
                    "suppressed_by_key": stats.get("suppressed_by_key", {}),
                    "last_event": last_event,
                    "raw_json": json.dumps(stats, sort_keys=True),
                    "suppressed_event_categories": dict(self._suppressed_event_categories),
                },
            )

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

    async def _emit_lighting_hold_events(self) -> None:
        for room_id, room_map in self._lighting_room_maps().items():
            if not room_map.get("enable_manual_hold", True):
                continue

            current = self._is_lighting_room_hold_on(room_id)
            if room_id not in self._lighting_hold_seen_state:
                self._lighting_hold_seen_state[room_id] = current
                continue

            previous = self._lighting_hold_seen_state[room_id]
            if previous == current:
                continue

            self._lighting_hold_seen_state[room_id] = current
            self._queue_event(
                HeimaEvent(
                    type="lighting.hold_on" if current else "lighting.hold_off",
                    key=f"lighting.hold.{room_id}",
                    severity="info",
                    title="Lighting hold enabled" if current else "Lighting hold disabled",
                    message=(
                        f"Manual lighting hold {'enabled' if current else 'disabled'} "
                        f"for room '{room_id}'"
                    ),
                    context={"room": room_id},
                )
            )

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
        observations = [self._normalizer.presence(entity_id) for entity_id in sources]
        active_count = sum(1 for obs in observations if obs.state == "on")
        fused = self._normalizer.derive(
            kind="presence",
            inputs=observations,
            strategy_cfg={"plugin_id": "builtin.quorum", "required": int(required)},
            context={"source": "group_presence"},
        )
        return fused.state == "on", active_count

    def _compute_room_occupancy(self, room_cfg: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        room_id = str(room_cfg.get("room_id", ""))
        mode = self._room_occupancy_mode(room_cfg)
        if mode == "none":
            return False, {
                "room_id": room_id,
                "occupancy_mode": "none",
                "source_observations": [],
                "fused_observation": None,
                "plugin_id": None,
                "candidate_state": "off",
                "candidate_since": None,
                "effective_state": "off",
                "effective_since": None,
                "on_dwell_s": None,
                "off_dwell_s": None,
                "max_on_s": None,
                "forced_off_by_max_on": False,
            }

        sources = list(room_cfg.get("sources", []))
        if not sources:
            return False, {
                "room_id": room_id,
                "occupancy_mode": mode,
                "source_observations": [],
                "fused_observation": None,
                "plugin_id": None,
                "candidate_state": "unknown",
                "candidate_since": None,
                "effective_state": "off",
                "effective_since": None,
                "on_dwell_s": int(room_cfg.get("on_dwell_s", 5)),
                "off_dwell_s": int(room_cfg.get("off_dwell_s", 120)),
                "max_on_s": room_cfg.get("max_on_s"),
                "forced_off_by_max_on": False,
            }

        logic = str(room_cfg.get("logic", "any_of"))
        plugin_id = "builtin.all_of" if logic == "all_of" else "builtin.any_of"

        observations = [self._normalizer.presence(entity_id) for entity_id in sources]
        fused = self._normalizer.derive(
            kind="presence",
            inputs=observations,
            strategy_cfg={"plugin_id": plugin_id},
            context={"room_id": room_id},
        )

        candidate_state = fused.state if fused.state in {"on", "off"} else "unknown"
        now = time.monotonic()
        previous_candidate = self._occupancy_room_candidate_state.get(room_id)
        if previous_candidate != candidate_state:
            self._occupancy_room_candidate_state[room_id] = candidate_state
            self._occupancy_room_candidate_since[room_id] = now

        candidate_since = self._occupancy_room_candidate_since.get(room_id, now)
        on_dwell_s = int(room_cfg.get("on_dwell_s", 5))
        off_dwell_s = int(room_cfg.get("off_dwell_s", 120))
        max_on_s_raw = room_cfg.get("max_on_s")
        max_on_s = int(max_on_s_raw) if max_on_s_raw not in (None, "") else None

        effective_state = self._occupancy_room_effective_state.get(room_id)
        if effective_state is None:
            effective_state = "on" if candidate_state == "on" else "off"
            self._occupancy_room_effective_state[room_id] = effective_state
            self._occupancy_room_effective_since[room_id] = now
        elif candidate_state in {"on", "off"} and candidate_state != effective_state:
            dwell = on_dwell_s if candidate_state == "on" else off_dwell_s
            if (now - candidate_since) >= max(0, dwell):
                effective_state = candidate_state
                self._occupancy_room_effective_state[room_id] = effective_state
                self._occupancy_room_effective_since[room_id] = now

        forced_off_by_max_on = False
        effective_since = self._occupancy_room_effective_since.get(room_id, now)
        if max_on_s is not None and max_on_s > 0 and effective_state == "on":
            if (now - effective_since) >= max_on_s:
                forced_off_by_max_on = True
                effective_state = "off"
                self._occupancy_room_effective_state[room_id] = "off"
                self._occupancy_room_effective_since[room_id] = now
                self._queue_event(
                    HeimaEvent(
                        type="occupancy.max_on_timeout",
                        key=f"occupancy.max_on_timeout.{room_id}",
                        severity="info",
                        title="Room occupancy max-on timeout",
                        message=f"Room '{room_id}' occupancy forced off after max_on_s timeout.",
                        context={"room": room_id, "max_on_s": max_on_s},
                    )
                )
        effective_since = self._occupancy_room_effective_since.get(room_id, now)

        trace = {
            "room_id": room_id,
            "occupancy_mode": mode,
            "source_observations": [obs.as_dict() for obs in observations],
            "fused_observation": fused.as_dict(),
            "plugin_id": fused.plugin_id,
            "candidate_state": candidate_state,
            "candidate_since": candidate_since,
            "effective_state": effective_state,
            "effective_since": effective_since,
            "on_dwell_s": on_dwell_s,
            "off_dwell_s": off_dwell_s,
            "max_on_s": max_on_s,
            "forced_off_by_max_on": forced_off_by_max_on,
        }
        return effective_state == "on", trace

    def _is_entity_home(self, entity_id: str | None) -> bool:
        return self._normalizer.presence(entity_id).state == "on"

    def _is_presence_on(self, entity_id: str | None) -> bool:
        return self._normalizer.presence(entity_id).state == "on"

    def _is_on_any(self, entity_ids: list[str]) -> bool:
        return any(self._normalizer.boolean_signal(entity_id).state == "on" for entity_id in entity_ids)

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

    def _room_occupancy_mode(self, room_cfg: dict[str, Any]) -> str:
        mode = str(room_cfg.get("occupancy_mode", "derived") or "derived")
        return mode if mode in {"derived", "none"} else "derived"

    def diagnostics(self) -> dict[str, Any]:
        return {
            "snapshot": self._snapshot.as_dict(),
            "apply_plan": {
                "plan_id": self._apply_plan.plan_id,
                "steps": [
                    {
                        "domain": step.domain,
                        "target": step.target,
                        "action": step.action,
                        "params": dict(step.params),
                        "reason": step.reason,
                    }
                    for step in self._apply_plan.steps
                ],
            },
            "lighting": {
                "zone_trace": dict(self._lighting_zone_trace),
                "room_trace": {room_id: list(items) for room_id, items in self._lighting_room_trace.items()},
                "conflicts_last_eval": list(self._lighting_conflicts_last_eval),
                "last_scene_by_room": dict(self._lighting_last_scene),
                "last_apply_ts_by_room": dict(self._lighting_last_ts),
                "hold_seen_state_by_room": dict(self._lighting_hold_seen_state),
            },
            "events": self._events.stats.as_dict(),
            "occupancy": {
                "room_trace": dict(self._occupancy_room_trace),
            },
            "security": {
                "observation_trace": dict(self._security_observation_trace),
            },
        }
