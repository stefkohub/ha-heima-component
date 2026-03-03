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
from ..entities.registry import build_registry
from ..models import HeimaOptions
from .contracts import ApplyPlan, ApplyStep, HeimaEvent
from .lighting import pick_scene_for_intent_with_trace, resolve_zone_intent
from .normalization.config import (
    GROUP_PRESENCE_STRATEGY_CONTRACT,
    HOUSE_SIGNAL_STRATEGY_CONTRACT,
    ROOM_OCCUPANCY_STRATEGY_CONTRACT,
    SECURITY_CORROBORATION_STRATEGY_CONTRACT,
    build_signal_set_strategy_cfg_for_contract,
)
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
_HEATING_MIN_SECONDS_BETWEEN_APPLIES = 60


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
        self._heating_trace: dict[str, Any] = {}
        self._heating_last_target_temp: float | None = None
        self._heating_last_apply_ts: float | None = None
        self._heating_last_reported_phase: str | None = None
        self._heating_last_reported_target: float | None = None
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
        self._group_presence_trace: dict[str, dict[str, Any]] = {}
        self._house_signals_trace: dict[str, dict[str, Any]] = {}
        self._next_timed_recheck_at: float | None = None
        self._security_observation_trace: dict[str, Any] = {}
        self._security_corroboration_trace: dict[str, Any] = {}
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

        heating = options.get(OPT_HEATING, {})
        for key in (
            "climate_entity",
            "outdoor_temperature_entity",
            "vacation_hours_from_start_entity",
            "vacation_hours_to_end_entity",
            "vacation_total_hours_entity",
            "vacation_is_long_entity",
        ):
            value = heating.get(key)
            if value:
                tracked.add(str(value))

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
        if "heima_heating_state" in self._state.sensors:
            self._state.sensors["heima_heating_state"] = "idle"
        if "heima_heating_reason" in self._state.sensors:
            self._state.sensors["heima_heating_reason"] = "not_configured"
        if "heima_heating_phase" in self._state.sensors:
            self._state.sensors["heima_heating_phase"] = "normal"
        if "heima_heating_branch" in self._state.sensors:
            self._state.sensors["heima_heating_branch"] = "disabled"
        if "heima_heating_target_temp" in self._state.sensors:
            self._state.sensors["heima_heating_target_temp"] = None
        if "heima_heating_current_setpoint" in self._state.sensors:
            self._state.sensors["heima_heating_current_setpoint"] = None
        if "heima_heating_last_applied_target" in self._state.sensors:
            self._state.sensors["heima_heating_last_applied_target"] = None

    def _compute_snapshot(self, reason: str) -> DecisionSnapshot:
        options = dict(self._entry.options)
        now = datetime.now(timezone.utc).isoformat()
        self._next_timed_recheck_at = None

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
            anon_fused, active_count = self._compute_group_presence(
                anon_sources,
                required,
                strategy=str(anon_cfg.get("group_strategy", "quorum") or "quorum"),
                weight_threshold=anon_cfg.get("weight_threshold"),
                source_weights=anon_cfg.get("source_weights"),
                trace_key="anonymous",
            )
            anon_home = anon_fused.state == "on"
            anon_confidence = int(anon_fused.confidence)
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

        vacation_mode = self._compute_house_signal(
            "vacation_mode",
            ["input_boolean.vacation_mode"],
        )
        guest_mode = self._compute_house_signal(
            "guest_mode",
            ["input_boolean.guest_mode"],
        )
        sleep_window = self._compute_house_signal(
            "sleep_window",
            ["binary_sensor.sleep_window"],
        )
        relax_mode = self._compute_house_signal(
            "relax_mode",
            ["binary_sensor.relax_mode"],
        )
        work_window = self._compute_house_signal(
            "work_window",
            ["binary_sensor.work_window"],
        )

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

        self._compute_heating_runtime(house_state=house_state)

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
            security_state=security_state,
            notes=f"reason={reason}",
        )

    def next_dwell_recheck_delay_s(self) -> float | None:
        """Return seconds until next timed recheck should be evaluated."""
        if self._next_timed_recheck_at is None:
            return None
        return max(0.0, self._next_timed_recheck_at - time.monotonic())

    def _schedule_timed_recheck_deadline(self, deadline: float) -> None:
        if self._next_timed_recheck_at is None or deadline < self._next_timed_recheck_at:
            self._next_timed_recheck_at = deadline

    def _compute_heating_runtime(self, *, house_state: str) -> None:
        heating_cfg = dict(self._entry.options.get(OPT_HEATING, {}))
        if not heating_cfg:
            self._heating_trace = {
                "configured": False,
                "state": "idle",
                "reason": "not_configured",
                "phase": "normal",
                "target_temperature": None,
            }
            return

        climate_entity = str(heating_cfg.get("climate_entity", "")).strip()
        apply_mode = str(heating_cfg.get("apply_mode", "delegate_to_scheduler") or "delegate_to_scheduler")
        branches = heating_cfg.get("override_branches", {})
        branch_cfg = dict(branches.get(house_state, {})) if isinstance(branches, dict) else {}
        branch_type = str(branch_cfg.get("branch", "disabled") or "disabled")
        manual_guard_enabled = bool(heating_cfg.get("manual_override_guard", True))
        manual_hold = bool(self._state.get_binary("heima_heating_manual_hold"))
        temperature_step = self._coerce_positive_float(heating_cfg.get("temperature_step"), default=0.5)
        current_setpoint = self._current_climate_setpoint(climate_entity)
        outdoor_temperature = self._coerce_float_from_entity(heating_cfg.get("outdoor_temperature_entity"))

        previous_reason = self._state.get_sensor("heima_heating_reason")
        state = "delegated"
        reason = "normal_scheduler_delegate"
        phase = "normal"
        target_temperature: float | None = None
        apply_allowed = False
        applying_guard = False
        skip_small_delta = False
        skip_rate_limited = False
        vacation_meta: dict[str, Any] = {}

        if branch_type == "scheduler_delegate":
            reason = "scheduler_delegate_branch"
            phase = "scheduler_delegate"
        elif branch_type == "fixed_target":
            phase = "fixed_target"
            target_temperature = self._coerce_positive_float(branch_cfg.get("target_temperature"), default=None)
            if target_temperature is None:
                state = "inactive"
                reason = "invalid_target_temperature"
                applying_guard = True
            else:
                (
                    state,
                    reason,
                    apply_allowed,
                    applying_guard,
                    skip_small_delta,
                    skip_rate_limited,
                ) = self._finalize_heating_target(
                    branch_reason="fixed_target_branch",
                    target_temperature=target_temperature,
                    apply_mode=apply_mode,
                    manual_guard_enabled=manual_guard_enabled,
                    manual_hold=manual_hold,
                    current_setpoint=current_setpoint,
                    temperature_step=temperature_step,
                )
        elif branch_type == "vacation_curve":
            (
                target_temperature,
                phase,
                vacation_meta,
                vacation_error,
            ) = self._resolve_vacation_curve_target(
                heating_cfg=heating_cfg,
                branch_cfg=branch_cfg,
                outdoor_temperature=outdoor_temperature,
                temperature_step=temperature_step,
            )
            if vacation_error:
                state = "inactive"
                reason = vacation_error
                applying_guard = True
            elif target_temperature is None:
                state = "inactive"
                reason = "vacation_curve_not_resolved"
                applying_guard = True
            else:
                (
                    state,
                    reason,
                    apply_allowed,
                    applying_guard,
                    skip_small_delta,
                    skip_rate_limited,
                ) = self._finalize_heating_target(
                    branch_reason="vacation_curve_branch",
                    target_temperature=target_temperature,
                    apply_mode=apply_mode,
                    manual_guard_enabled=manual_guard_enabled,
                    manual_hold=manual_hold,
                    current_setpoint=current_setpoint,
                    temperature_step=temperature_step,
                )
        else:
            branch_type = "disabled"

        self._state.set_sensor("heima_heating_state", state)
        self._state.set_sensor("heima_heating_reason", reason)
        self._state.set_sensor("heima_heating_phase", phase)
        self._state.set_sensor("heima_heating_branch", branch_type)
        self._state.set_sensor("heima_heating_target_temp", target_temperature)
        self._state.set_sensor("heima_heating_current_setpoint", current_setpoint)
        self._state.set_sensor("heima_heating_last_applied_target", self._heating_last_target_temp)
        self._state.set_binary("heima_heating_applying_guard", applying_guard)

        self._heating_trace = {
            "configured": True,
            "climate_entity": climate_entity,
            "apply_mode": apply_mode,
            "current_house_state": house_state,
            "selected_branch": branch_type,
            "current_setpoint": current_setpoint,
            "outdoor_temperature": outdoor_temperature,
            "target_temperature": target_temperature,
            "temperature_step": temperature_step,
            "manual_override_guard_enabled": manual_guard_enabled,
            "manual_hold": manual_hold,
            "state": state,
            "reason": reason,
            "phase": phase,
            "apply_allowed": apply_allowed,
            "applying_guard": applying_guard,
            "skip_small_delta": skip_small_delta,
            "skip_rate_limited": skip_rate_limited,
            "rate_limit_window_s": _HEATING_MIN_SECONDS_BETWEEN_APPLIES,
            "last_applied_target": self._heating_last_target_temp,
            "last_apply_ts": self._heating_last_apply_ts,
            "vacation": dict(vacation_meta),
        }
        self._queue_heating_runtime_events(
            selected_branch=branch_type,
            previous_reason=str(previous_reason) if previous_reason not in (None, "") else None,
            reason=reason,
            phase=phase,
            target_temperature=target_temperature,
            apply_allowed=apply_allowed,
            skip_small_delta=skip_small_delta,
        )

    def _queue_heating_runtime_events(
        self,
        *,
        selected_branch: str,
        previous_reason: str | None,
        reason: str,
        phase: str,
        target_temperature: float | None,
        apply_allowed: bool,
        skip_small_delta: bool,
    ) -> None:
        if selected_branch == "vacation_curve" and self._heating_last_reported_phase != phase:
            self._queue_event(
                HeimaEvent(
                    type="heating.vacation_phase_changed",
                    key="heating.vacation_phase_changed",
                    severity="info",
                    title="Heating vacation phase changed",
                    message=f"Heating vacation phase changed to '{phase}'.",
                    context={"phase": phase},
                )
            )
            self._heating_last_reported_phase = phase
        elif selected_branch != "vacation_curve":
            self._heating_last_reported_phase = None

        if apply_allowed and target_temperature is not None and self._heating_last_reported_target != target_temperature:
            self._queue_event(
                HeimaEvent(
                    type="heating.target_changed",
                    key="heating.target_changed",
                    severity="info",
                    title="Heating target changed",
                    message=f"Heating target updated to {target_temperature}.",
                    context={
                        "target_temperature": target_temperature,
                        "branch": selected_branch,
                        "phase": phase,
                    },
                )
            )
            self._heating_last_reported_target = target_temperature

        if reason == "manual_override_blocked" and previous_reason != "manual_override_blocked":
            self._queue_event(
                HeimaEvent(
                    type="heating.manual_override_blocked",
                    key="heating.manual_override_blocked",
                    severity="info",
                    title="Heating blocked by manual override",
                    message="Heating apply skipped because manual override is active.",
                    context={"branch": selected_branch},
                )
            )

        if skip_small_delta and previous_reason != "small_delta_skip":
            self._queue_event(
                HeimaEvent(
                    type="heating.apply_skipped_small_delta",
                    key="heating.apply_skipped_small_delta",
                    severity="info",
                    title="Heating apply skipped",
                    message="Heating target change is below the configured temperature step.",
                    context={
                        "branch": selected_branch,
                        "target_temperature": target_temperature,
                    },
                )
            )

        if reason == "apply_rate_limited" and previous_reason != "apply_rate_limited":
            self._queue_event(
                HeimaEvent(
                    type="heating.apply_rate_limited",
                    key="heating.apply_rate_limited",
                    severity="info",
                    title="Heating apply rate-limited",
                    message="Heating apply skipped because the minimum apply interval is still active.",
                    context={
                        "branch": selected_branch,
                        "target_temperature": target_temperature,
                    },
                )
            )

        if reason == "vacation_bindings_unavailable" and previous_reason != "vacation_bindings_unavailable":
            self._queue_event(
                HeimaEvent(
                    type="heating.vacation_bindings_unavailable",
                    key="heating.vacation_bindings_unavailable",
                    severity="warn",
                    title="Heating vacation bindings unavailable",
                    message="Heating vacation branch could not compute a target because required bindings are unavailable.",
                    context={"branch": selected_branch},
                )
            )

    def _finalize_heating_target(
        self,
        *,
        branch_reason: str,
        target_temperature: float,
        apply_mode: str,
        manual_guard_enabled: bool,
        manual_hold: bool,
        current_setpoint: float | None,
        temperature_step: float,
    ) -> tuple[str, str, bool, bool, bool, bool]:
        if apply_mode != "set_temperature":
            return ("delegated", "apply_mode_delegate_to_scheduler", False, False, False, False)
        if manual_guard_enabled and manual_hold:
            return ("blocked", "manual_override_blocked", False, True, False, False)

        diff = (
            None
            if current_setpoint is None
            else abs(float(target_temperature) - float(current_setpoint))
        )
        if diff is not None and diff < temperature_step:
            return ("idle", "small_delta_skip", False, True, True, False)

        if self._heating_last_target_temp == target_temperature and self._heating_last_apply_ts is not None:
            if (time.monotonic() - self._heating_last_apply_ts) < _HEATING_MIN_SECONDS_BETWEEN_APPLIES:
                return ("idle", "apply_rate_limited", False, True, False, True)

        return ("target_active", branch_reason, True, False, False, False)

    def _resolve_vacation_curve_target(
        self,
        *,
        heating_cfg: dict[str, Any],
        branch_cfg: dict[str, Any],
        outdoor_temperature: float | None,
        temperature_step: float,
    ) -> tuple[float | None, str, dict[str, Any], str | None]:
        hours_from = self._coerce_float_from_entity(heating_cfg.get("vacation_hours_from_start_entity"))
        hours_to = self._coerce_float_from_entity(heating_cfg.get("vacation_hours_to_end_entity"))
        total_hours = self._coerce_float_from_entity(heating_cfg.get("vacation_total_hours_entity"))
        explicit_is_long = self._coerce_bool_from_entity(heating_cfg.get("vacation_is_long_entity"))
        ramp_down = self._coerce_non_negative_float(branch_cfg.get("vacation_ramp_down_h"), default=None)
        ramp_up = self._coerce_non_negative_float(branch_cfg.get("vacation_ramp_up_h"), default=None)
        min_total_hours_for_ramp = self._coerce_non_negative_float(
            branch_cfg.get("vacation_min_total_hours_for_ramp"),
            default=None,
        )
        min_temp = self._coerce_positive_float(branch_cfg.get("vacation_min_temp"), default=None)
        comfort_temp = self._coerce_positive_float(branch_cfg.get("vacation_comfort_temp"), default=None)
        start_temp = self._coerce_positive_float(branch_cfg.get("vacation_start_temp"), default=None)

        if None in (
            hours_from,
            hours_to,
            total_hours,
            ramp_down,
            ramp_up,
            min_total_hours_for_ramp,
            min_temp,
            comfort_temp,
            start_temp,
            outdoor_temperature,
        ):
            return (None, "vacation_curve", {}, "vacation_bindings_unavailable")

        is_long = (
            explicit_is_long
            if explicit_is_long is not None
            else bool(total_hours >= min_total_hours_for_ramp)
        )
        min_safety = self._heating_vacation_min_safety(
            min_temp=min_temp,
            outdoor_temperature=outdoor_temperature,
        )

        if not is_long:
            phase = "eco_only"
            raw_target = min_safety
        else:
            eco = min_safety
            raw_target = eco
            phase = "cruise"
            if total_hours <= 0:
                phase = "cruise"
            elif ramp_down > 0 and hours_from < ramp_down:
                raw_target = start_temp + (eco - start_temp) * (hours_from / ramp_down)
                phase = "ramp_down"
            elif ramp_up > 0 and hours_to < ramp_up:
                raw_target = eco + (comfort_temp - eco) * (1 - (hours_to / ramp_up))
                phase = "ramp_up"

        quantized = round(raw_target / temperature_step) * temperature_step
        target = round(float(quantized), 2)
        return (
            target,
            phase,
            {
                "hours_from_start": hours_from,
                "hours_to_end": hours_to,
                "total_hours": total_hours,
                "is_long": is_long,
                "ramp_down_h": ramp_down,
                "ramp_up_h": ramp_up,
                "min_total_hours_for_ramp": min_total_hours_for_ramp,
                "min_temp": min_temp,
                "comfort_temp": comfort_temp,
                "start_temp": start_temp,
                "raw_target": round(float(raw_target), 4),
                "quantized_target": target,
                "min_safety": min_safety,
            },
            None,
        )

    @staticmethod
    def _heating_vacation_min_safety(*, min_temp: float, outdoor_temperature: float) -> float:
        if outdoor_temperature <= 0:
            return max(min_temp, 17.0)
        if outdoor_temperature <= 3:
            return max(min_temp, 16.5)
        return min_temp

    def _current_climate_setpoint(self, entity_id: str) -> float | None:
        return self._coerce_positive_float(self._state_attr(entity_id, "temperature"), default=None)

    def _state_attr(self, entity_id: str | None, attr_name: str) -> Any:
        if not entity_id:
            return None
        state = self._hass.states.get(entity_id)
        if state is None:
            return None
        attrs = getattr(state, "attributes", {}) or {}
        if not isinstance(attrs, dict):
            return None
        return attrs.get(attr_name)

    def _coerce_float_from_entity(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state = self._hass.states.get(str(entity_id))
        if state is None:
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _coerce_bool_from_entity(self, entity_id: str | None) -> bool | None:
        if not entity_id:
            return None
        if self._hass.states.get(str(entity_id)) is None:
            return None
        obs = self._normalizer.boolean_signal(str(entity_id))
        if obs.state == "unknown":
            return None
        return obs.state == "on"

    @staticmethod
    def _coerce_positive_float(value: Any, *, default: float | None) -> float | None:
        if value in (None, ""):
            return default
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if parsed <= 0:
            return default
        return parsed

    @staticmethod
    def _coerce_non_negative_float(value: Any, *, default: float | None) -> float | None:
        if value in (None, ""):
            return default
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if parsed < 0:
            return default
        return parsed

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

        heating_trace = dict(self._heating_trace)
        if heating_trace.get("configured") and heating_trace.get("apply_allowed"):
            climate_entity = str(heating_trace.get("climate_entity", "")).strip()
            target_temperature = heating_trace.get("target_temperature")
            if climate_entity and isinstance(target_temperature, (int, float)):
                steps.append(
                    ApplyStep(
                        domain="heating",
                        target=climate_entity,
                        action="climate.set_temperature",
                        params={
                            "entity_id": climate_entity,
                            "hvac_mode": "heat",
                            "temperature": float(target_temperature),
                        },
                        reason=f"branch:{heating_trace.get('selected_branch', 'disabled')}",
                    )
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

            if step.action == "climate.set_temperature":
                climate_entity = step.params.get("entity_id")
                if not isinstance(climate_entity, str) or not climate_entity.startswith("climate."):
                    continue

                if self._hass.states.get(climate_entity) is None:
                    _LOGGER.warning("Skipping missing climate entity: %s", climate_entity)
                    continue
                try:
                    await self._hass.services.async_call(
                        "climate",
                        "set_temperature",
                        dict(step.params),
                        blocking=False,
                    )
                    self._heating_last_target_temp = (
                        float(step.params["temperature"])
                        if isinstance(step.params.get("temperature"), (int, float))
                        else self._heating_last_target_temp
                    )
                    self._heating_last_apply_ts = time.monotonic()
                    self._state.set_sensor("heima_heating_last_applied_target", self._heating_last_target_temp)
                    continue
                except ServiceNotFound:
                    _LOGGER.warning(
                        "Skipping heating apply during startup/race: service climate.set_temperature not available"
                    )
                    continue
                except Exception:
                    _LOGGER.exception("Heating apply failed for climate '%s'", climate_entity)
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
        corroboration_inputs = [
            self._normalizer.boolean_value(
                has_room_evidence,
                source_key="security:derived_room_evidence",
                reason="derived_room_occupied" if has_room_evidence else "no_derived_room_occupied",
            ),
            self._normalizer.boolean_value(
                has_anonymous_evidence,
                source_key="security:anonymous_presence_evidence",
                reason="anonymous_presence_on" if has_anonymous_evidence else "anonymous_presence_off",
            ),
        ]
        corroboration = self._normalizer.derive(
            kind="boolean_signal",
            inputs=corroboration_inputs,
            strategy_cfg=build_signal_set_strategy_cfg_for_contract(
                contract=SECURITY_CORROBORATION_STRATEGY_CONTRACT,
            ),
            context={"source": "security_corroboration"},
        )
        self._security_corroboration_trace = {
            "source_observations": [obs.as_dict() for obs in corroboration_inputs],
            "fused_observation": corroboration.as_dict(),
            "plugin_id": corroboration.plugin_id,
            "used_plugin_fallback": corroboration.reason == "plugin_error_fallback",
        }
        if policy == "smart":
            mismatch_active = mismatch_active and corroboration.state == "on"

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
            self._schedule_timed_recheck_deadline(self._occupancy_home_no_room_since + persist_s)
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
            self._schedule_timed_recheck_deadline(self._occupancy_room_no_home_since[room_id] + persist_s)
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
        self._schedule_timed_recheck_deadline(self._security_armed_away_but_home_since + persist_s)
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
            slug = str(person_cfg.get("slug", ""))
            trace_key = f"person:{slug}" if slug else "person:unknown"
            fused, active_count = self._compute_group_presence(
                sources,
                required,
                strategy=str(person_cfg.get("group_strategy", "quorum") or "quorum"),
                weight_threshold=person_cfg.get("weight_threshold"),
                source_weights=person_cfg.get("source_weights"),
                trace_key=trace_key,
            )
            is_home = fused.state == "on"
            confidence = int(fused.confidence)
            return is_home, "quorum", confidence

        slug = str(person_cfg.get("slug", ""))
        override = self._state.get_select(f"heima_person_{slug}_override")
        if override == "force_home":
            return True, "manual", 100
        if override == "force_away":
            return False, "manual", 100
        return False, "manual", 0

    def _compute_group_presence(
        self,
        sources: list[str],
        required: int,
        *,
        strategy: str = "quorum",
        weight_threshold: Any = None,
        source_weights: Any = None,
        trace_key: str | None = None,
    ) -> tuple[DerivedObservation, int]:
        observations = [self._normalizer.presence(entity_id) for entity_id in sources]
        active_count = sum(1 for obs in observations if obs.state == "on")
        group_strategy = str(strategy or "quorum")
        strategy_cfg = build_signal_set_strategy_cfg_for_contract(
            contract=GROUP_PRESENCE_STRATEGY_CONTRACT,
            strategy=group_strategy,
            required=int(required),
            weight_threshold=weight_threshold,
            source_weights=source_weights,
            fallback_state="off",
        )
        fused = self._normalizer.derive(
            kind="presence",
            inputs=observations,
            strategy_cfg=strategy_cfg,
            context={"source": "group_presence"},
        )
        if trace_key:
            self._group_presence_trace[trace_key] = {
                "source_observations": [obs.as_dict() for obs in observations],
                "fused_observation": fused.as_dict(),
                "plugin_id": fused.plugin_id,
                "group_strategy": group_strategy,
                "required": int(required),
                "weight_threshold": (
                    float(weight_threshold)
                    if group_strategy == "weighted_quorum" and weight_threshold not in (None, "")
                    else None
                ),
                "configured_source_weights": (
                    dict(source_weights) if group_strategy == "weighted_quorum" and isinstance(source_weights, dict) else {}
                ),
                "active_count": active_count,
                "used_plugin_fallback": fused.reason == "plugin_error_fallback",
            }
        return fused, active_count

    def _compute_house_signal(self, trace_key: str, entity_ids: list[str]) -> bool:
        observations = [self._normalizer.boolean_signal(entity_id) for entity_id in entity_ids]
        fused = self._normalizer.derive(
            kind="boolean_signal",
            inputs=observations,
            strategy_cfg=build_signal_set_strategy_cfg_for_contract(
                contract=HOUSE_SIGNAL_STRATEGY_CONTRACT,
            ),
            context={"source": "house_signal", "signal": trace_key},
        )
        self._house_signals_trace[trace_key] = {
            "source_observations": [obs.as_dict() for obs in observations],
            "fused_observation": fused.as_dict(),
            "plugin_id": fused.plugin_id,
            "used_plugin_fallback": fused.reason == "plugin_error_fallback",
        }
        return fused.state == "on"

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
        observations = [self._normalizer.presence(entity_id) for entity_id in sources]
        strategy_cfg = build_signal_set_strategy_cfg_for_contract(
            contract=ROOM_OCCUPANCY_STRATEGY_CONTRACT,
            strategy=logic,
            weight_threshold=room_cfg.get("weight_threshold"),
            source_weights=room_cfg.get("source_weights"),
            fallback_state="off",
        )
        fused = self._normalizer.derive(
            kind="presence",
            inputs=observations,
            strategy_cfg=strategy_cfg,
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
            dwell = max(0, dwell)
            if (now - candidate_since) >= dwell:
                effective_state = candidate_state
                self._occupancy_room_effective_state[room_id] = effective_state
                self._occupancy_room_effective_since[room_id] = now
            elif dwell > 0:
                deadline = candidate_since + dwell
                self._schedule_timed_recheck_deadline(deadline)

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
            "used_plugin_fallback": fused.reason == "plugin_error_fallback",
            "configured_source_weights": (
                dict(room_cfg.get("source_weights", {})) if logic == "weighted_quorum" else {}
            ),
            "effective_source_weights": dict(fused.evidence.get("weights", {}))
            if isinstance(fused.evidence, dict)
            else {},
            "source_weight_contributions": [
                {
                    "entity_id": obs.source_entity_id,
                    "state": obs.state,
                    "weight": (
                        fused.evidence.get("weights", {}).get(obs.source_entity_id or "", 1.0)
                        if isinstance(fused.evidence, dict)
                        else 1.0
                    ),
                    "contributes_to_on": obs.state == "on",
                }
                for obs in observations
            ],
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
            "heating": dict(self._heating_trace),
            "events": self._events.stats.as_dict(),
            "presence": {
                "group_trace": dict(self._group_presence_trace),
            },
            "house_signals": {
                "trace": dict(self._house_signals_trace),
            },
            "occupancy": {
                "room_trace": dict(self._occupancy_room_trace),
            },
            "security": {
                "observation_trace": dict(self._security_observation_trace),
                "corroboration_trace": dict(self._security_corroboration_trace),
            },
            "normalization": self._normalizer.diagnostics(),
        }
