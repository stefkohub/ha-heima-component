"""Notification/event pipeline helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant

from ..const import EVENT_HEIMA_EVENT
from .contracts import HeimaEvent


@dataclass
class EventPipelineStats:
    """Simple runtime counters for event pipeline behavior."""

    emitted: int = 0
    dropped_dedup: int = 0
    dropped_rate_limited: int = 0
    last_event: HeimaEvent | None = None
    suppressed_by_key: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "emitted": self.emitted,
            "dropped_dedup": self.dropped_dedup,
            "dropped_rate_limited": self.dropped_rate_limited,
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

        for route in routes:
            if not route:
                continue
            await self._hass.services.async_call(
                "notify",
                route,
                {
                    "title": event.title,
                    "message": event.message,
                    "data": {
                        "heima_event_type": event.type,
                        "heima_event_key": event.key,
                        "heima_severity": event.severity,
                        "heima_context": event.context,
                    },
                },
                blocking=False,
            )

        return True
