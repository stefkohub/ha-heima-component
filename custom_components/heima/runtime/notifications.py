"""Notification/event pipeline helpers."""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound

from ..const import EVENT_HEIMA_EVENT
from .contracts import HeimaEvent

_LOGGER = logging.getLogger(__name__)
_MAX_DEFERRED_ROUTE_DELIVERIES = 128


@dataclass
class EventPipelineStats:
    """Simple runtime counters for event pipeline behavior."""

    emitted: int = 0
    dropped_dedup: int = 0
    dropped_rate_limited: int = 0
    notify_route_unavailable: int = 0
    notify_route_errors: int = 0
    notify_route_deferred_dropped: int = 0
    notify_route_delivered: int = 0
    notify_route_retried: int = 0
    last_event: HeimaEvent | None = None
    suppressed_by_key: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "emitted": self.emitted,
            "dropped_dedup": self.dropped_dedup,
            "dropped_rate_limited": self.dropped_rate_limited,
            "notify_route_unavailable": self.notify_route_unavailable,
            "notify_route_errors": self.notify_route_errors,
            "notify_route_deferred_dropped": self.notify_route_deferred_dropped,
            "notify_route_delivered": self.notify_route_delivered,
            "notify_route_retried": self.notify_route_retried,
            "last_event": self.last_event.as_dict() if self.last_event else None,
            "suppressed_by_key": dict(self.suppressed_by_key),
        }


class HeimaEventPipeline:
    """Deduplicates, rate-limits, and emits Heima events."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._last_seen_ts: dict[str, float] = {}
        self._last_emitted_ts: dict[str, float] = {}
        self._stats = EventPipelineStats()
        self._deferred_route_deliveries: deque[tuple[HeimaEvent, str]] = deque(
            maxlen=_MAX_DEFERRED_ROUTE_DELIVERIES
        )

    @property
    def stats(self) -> EventPipelineStats:
        return self._stats

    async def async_emit(
        self,
        event: HeimaEvent,
        *,
        routes: list[str],
        dedup_window_s: int,
        rate_limit_per_key_s: int,
    ) -> bool:
        now = time.monotonic()

        if dedup_window_s > 0:
            last_seen = self._last_seen_ts.get(event.key)
            if last_seen is not None and (now - last_seen) < dedup_window_s:
                self._stats.dropped_dedup += 1
                self._stats.suppressed_by_key[event.key] = (
                    self._stats.suppressed_by_key.get(event.key, 0) + 1
                )
                self._last_seen_ts[event.key] = now
                return False
            self._last_seen_ts[event.key] = now

        if rate_limit_per_key_s > 0:
            last_emit = self._last_emitted_ts.get(event.key)
            if last_emit is not None and (now - last_emit) < rate_limit_per_key_s:
                self._stats.dropped_rate_limited += 1
                self._stats.suppressed_by_key[event.key] = (
                    self._stats.suppressed_by_key.get(event.key, 0) + 1
                )
                return False

        self._last_emitted_ts[event.key] = now
        self._stats.emitted += 1
        self._stats.last_event = event

        payload = event.as_dict()
        self._hass.bus.async_fire(EVENT_HEIMA_EVENT, payload)

        await self._flush_deferred_route_deliveries()

        for route in routes:
            if not route:
                continue
            await self._deliver_or_defer_route(event=event, route=route, is_retry=False)

        return True

    async def _flush_deferred_route_deliveries(self) -> None:
        if not self._deferred_route_deliveries:
            return

        remaining: deque[tuple[HeimaEvent, str]] = deque(maxlen=_MAX_DEFERRED_ROUTE_DELIVERIES)
        while self._deferred_route_deliveries:
            event, route = self._deferred_route_deliveries.popleft()
            delivered = await self._try_deliver_route(event=event, route=route, is_retry=True)
            if not delivered:
                if len(remaining) == remaining.maxlen:
                    self._stats.notify_route_deferred_dropped += 1
                    continue
                remaining.append((event, route))

        self._deferred_route_deliveries = remaining

    async def _deliver_or_defer_route(self, *, event: HeimaEvent, route: str, is_retry: bool) -> None:
        delivered = await self._try_deliver_route(event=event, route=route, is_retry=is_retry)
        if delivered:
            return
        self._defer_route_delivery(event, route)

    async def _try_deliver_route(self, *, event: HeimaEvent, route: str, is_retry: bool) -> bool:
        if not self._notify_service_available(route):
            self._stats.notify_route_unavailable += 1
            _LOGGER.debug("Heima notify route unavailable (deferred): notify.%s", route)
            return False

        try:
            await self._hass.services.async_call(
                "notify",
                route,
                self._notify_payload(event),
                blocking=False,
            )
        except ServiceNotFound:
            # Race condition: service disappeared between availability check and call.
            self._stats.notify_route_unavailable += 1
            _LOGGER.warning("Heima notify route missing at dispatch time (deferred): notify.%s", route)
            return False
        except Exception:  # pragma: no cover - defensive runtime protection
            self._stats.notify_route_errors += 1
            _LOGGER.exception("Heima notify route dispatch failed for notify.%s", route)
            return True  # considered handled; do not block setup or loop forever on persistent errors

        self._stats.notify_route_delivered += 1
        if is_retry:
            self._stats.notify_route_retried += 1
        return True

    def _defer_route_delivery(self, event: HeimaEvent, route: str) -> None:
        item = (event, route)
        # Keep latest attempts; bounded queue avoids unbounded growth during long outages.
        if len(self._deferred_route_deliveries) == self._deferred_route_deliveries.maxlen:
            self._stats.notify_route_deferred_dropped += 1
        self._deferred_route_deliveries.append(item)

    def _notify_service_available(self, route: str) -> bool:
        services_obj = getattr(self._hass, "services", None)
        if services_obj is None:
            return False
        async_services = getattr(services_obj, "async_services", None)
        if not callable(async_services):
            return False
        notify_services = async_services().get("notify", {})
        return route in notify_services

    def _notify_payload(self, event: HeimaEvent) -> dict[str, Any]:
        return {
            "title": event.title,
            "message": event.message,
            "data": {
                "heima_event_type": event.type,
                "heima_event_key": event.key,
                "heima_severity": event.severity,
                "heima_context": event.context,
            },
        }
