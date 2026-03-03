"""Coordinator for Heima runtime."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .models import HeimaRuntimeState
from .runtime.engine import HeimaEngine

_LOGGER = logging.getLogger(__name__)


class HeimaCoordinator(DataUpdateCoordinator[HeimaRuntimeState]):
    """Owns the Heima runtime engine instance."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=None,  # push-based
        )
        self.entry = entry
        self.engine = HeimaEngine(hass, entry)
        self._unsub_state_changed = None
        self._unsub_dwell_recheck = None
        self.data = HeimaRuntimeState(
            health_ok=True,
            health_reason="booting",
            house_state="unknown",
            house_state_reason="",
            last_decision="",
            last_action="",
        )

    async def _async_update_data(self) -> HeimaRuntimeState:
        """Return current runtime state for coordinator refreshes.

        Heima is push-driven: state updates are produced by explicit runtime calls.
        """
        return self.data

    async def async_initialize(self) -> None:
        """Initialize runtime and publish base state."""
        await self.engine.async_initialize()
        self._subscribe_state_changes()
        self._schedule_dwell_recheck()
        self.data = HeimaRuntimeState(
            health_ok=self.engine.health.ok,
            health_reason=self.engine.health.reason,
            house_state=self.engine.snapshot.house_state,
            house_state_reason=self.engine.state.get_sensor("heima_house_state_reason") or "",
            last_decision="initialized",
            last_action="",
        )
        await self.async_refresh()

    async def async_reload_options(self) -> None:
        """Reload options and refresh state."""
        await self.engine.async_reload_options(self.entry)
        self._resubscribe_state_changes()
        self._schedule_dwell_recheck()
        self.data = HeimaRuntimeState(
            health_ok=self.engine.health.ok,
            health_reason=self.engine.health.reason,
            house_state=self.engine.snapshot.house_state,
            house_state_reason=self.engine.state.get_sensor("heima_house_state_reason") or "",
            last_decision="options_reloaded",
            last_action="",
        )
        await self.async_refresh()

    async def async_request_evaluation(self, reason: str) -> None:
        """Request an evaluation cycle."""
        snapshot = await self.engine.async_evaluate(reason=reason)
        self.data = HeimaRuntimeState(
            health_ok=self.engine.health.ok,
            health_reason=self.engine.health.reason,
            house_state=snapshot.house_state,
            house_state_reason=self.engine.state.get_sensor("heima_house_state_reason") or "",
            last_decision=f"evaluation_requested:{reason}",
            last_action="",
        )
        self._schedule_dwell_recheck()
        await self.async_refresh()

    async def async_emit_event(
        self,
        *,
        event_type: str,
        key: str,
        severity: str,
        title: str,
        message: str,
        context: dict | None = None,
        reason: str = "service:notify_event",
    ) -> bool:
        """Emit an event through the engine pipeline and refresh coordinator state."""
        emitted = await self.engine.async_emit_external_event(
            event_type=event_type,
            key=key,
            severity=severity,
            title=title,
            message=message,
            context=context or {},
        )
        self.data = HeimaRuntimeState(
            health_ok=self.engine.health.ok,
            health_reason=self.engine.health.reason,
            house_state=self.engine.snapshot.house_state,
            house_state_reason=self.engine.state.get_sensor("heima_house_state_reason") or "",
            last_decision=f"{reason}:{'emitted' if emitted else 'suppressed'}",
            last_action="event_emitted" if emitted else "event_suppressed",
        )
        await self.async_refresh()
        return emitted

    async def async_shutdown(self) -> None:
        """Shutdown runtime."""
        self._unsubscribe_state_changes()
        self._unsubscribe_dwell_recheck()
        await self.engine.async_shutdown()
        _LOGGER.debug("Heima runtime shutdown")

    def _resubscribe_state_changes(self) -> None:
        self._unsubscribe_state_changes()
        self._subscribe_state_changes()

    def _unsubscribe_state_changes(self) -> None:
        if self._unsub_state_changed:
            self._unsub_state_changed()
            self._unsub_state_changed = None

    def _unsubscribe_dwell_recheck(self) -> None:
        if self._unsub_dwell_recheck:
            self._unsub_dwell_recheck()
            self._unsub_dwell_recheck = None

    def _schedule_dwell_recheck(self) -> None:
        self._unsubscribe_dwell_recheck()
        delay = self.engine.next_dwell_recheck_delay_s()
        if delay is None:
            return
        delay = max(0.1, float(delay))

        @callback
        def _handle_dwell_recheck(_now) -> None:
            self._unsub_dwell_recheck = None
            self.hass.async_create_task(self.async_request_evaluation(reason="dwell_recheck"))

        self._unsub_dwell_recheck = async_call_later(self.hass, delay, _handle_dwell_recheck)

    def _subscribe_state_changes(self) -> None:
        tracked_entities = self.engine.tracked_entity_ids()

        @callback
        def _handle_state_changed(event: Event) -> None:
            entity_id = event.data.get("entity_id")
            if entity_id not in tracked_entities:
                return
            self.hass.async_create_task(
                self.async_request_evaluation(reason=f"state_changed:{entity_id}")
            )

        self._unsub_state_changed = self.hass.bus.async_listen("state_changed", _handle_state_changed)
