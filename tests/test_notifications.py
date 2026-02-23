from types import SimpleNamespace

import pytest

from custom_components.heima.runtime.contracts import HeimaEvent
from custom_components.heima.runtime.notifications import HeimaEventPipeline


class _FakeBus:
    def __init__(self):
        self.events = []

    def async_fire(self, event_type, data):
        self.events.append((event_type, data))


class _FakeServices:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, data, blocking))


@pytest.mark.asyncio
async def test_event_pipeline_deduplicates(monkeypatch):
    bus = _FakeBus()
    services = _FakeServices()
    hass = SimpleNamespace(bus=bus, services=services)
    pipeline = HeimaEventPipeline(hass)

    t = 100.0
    monkeypatch.setattr(
        "custom_components.heima.runtime.notifications.time.monotonic",
        lambda: t,
    )

    event = HeimaEvent(
        type="lighting.scene_missing",
        key="lighting.scene_missing.room1.scene_relax",
        severity="warn",
        title="x",
        message="x",
    )
    emitted = await pipeline.async_emit(
        event,
        routes=[],
        dedup_window_s=60,
        rate_limit_per_key_s=300,
    )
    assert emitted is True
    assert len(bus.events) == 1

    t = 110.0
    emitted = await pipeline.async_emit(
        HeimaEvent(
            type=event.type,
            key=event.key,
            severity=event.severity,
            title=event.title,
            message=event.message,
        ),
        routes=[],
        dedup_window_s=60,
        rate_limit_per_key_s=300,
    )
    assert emitted is False
    assert pipeline.stats.dropped_dedup == 1
    assert len(bus.events) == 1


@pytest.mark.asyncio
async def test_event_pipeline_rate_limits_after_dedup_window(monkeypatch):
    bus = _FakeBus()
    services = _FakeServices()
    hass = SimpleNamespace(bus=bus, services=services)
    pipeline = HeimaEventPipeline(hass)

    t = 100.0
    monkeypatch.setattr(
        "custom_components.heima.runtime.notifications.time.monotonic",
        lambda: t,
    )

    key = "lighting.hold.room1"
    for current_t in (100.0, 170.0):
        t = current_t
        emitted = await pipeline.async_emit(
            HeimaEvent(
                type="lighting.hold_on",
                key=key,
                severity="info",
                title="hold",
                message="hold",
            ),
            routes=[],
            dedup_window_s=60,
            rate_limit_per_key_s=300,
        )
        if current_t == 100.0:
            assert emitted is True
        else:
            assert emitted is False

    assert pipeline.stats.dropped_rate_limited == 1
    assert len(bus.events) == 1
