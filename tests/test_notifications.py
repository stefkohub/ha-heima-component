from types import SimpleNamespace

import pytest
from homeassistant.exceptions import ServiceNotFound

from custom_components.heima.runtime.contracts import HeimaEvent
from custom_components.heima.runtime.notifications import HeimaEventPipeline


class _FakeBus:
    def __init__(self):
        self.events = []

    def async_fire(self, event_type, data):
        self.events.append((event_type, data))


class _FakeServices:
    def __init__(self, available: dict[str, object] | None = None, fail_once: set[str] | None = None):
        self.calls = []
        self.available = dict(available or {})
        self.fail_once = set(fail_once or set())

    async def async_call(self, domain, service, data, blocking=False):
        if domain == "notify" and service in self.fail_once:
            self.fail_once.remove(service)
            raise ServiceNotFound(domain, service)
        self.calls.append((domain, service, data, blocking))

    def async_services(self):
        return {"notify": dict(self.available)}


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


@pytest.mark.asyncio
async def test_event_pipeline_defers_missing_notify_route_without_failing():
    bus = _FakeBus()
    services = _FakeServices(available={})
    hass = SimpleNamespace(bus=bus, services=services)
    pipeline = HeimaEventPipeline(hass)

    emitted = await pipeline.async_emit(
        HeimaEvent(
            type="debug.test",
            key="debug.test",
            severity="info",
            title="t",
            message="m",
        ),
        routes=["mobile_app_test"],
        dedup_window_s=0,
        rate_limit_per_key_s=0,
    )

    assert emitted is True
    assert len(bus.events) == 1
    assert services.calls == []
    assert pipeline.stats.notify_route_unavailable >= 1


@pytest.mark.asyncio
async def test_event_pipeline_retries_deferred_route_when_service_appears():
    bus = _FakeBus()
    services = _FakeServices(available={})
    hass = SimpleNamespace(bus=bus, services=services)
    pipeline = HeimaEventPipeline(hass)

    first = HeimaEvent(
        type="debug.first",
        key="debug.first",
        severity="info",
        title="first",
        message="first",
    )
    await pipeline.async_emit(
        first,
        routes=["mobile_app_test"],
        dedup_window_s=0,
        rate_limit_per_key_s=0,
    )
    assert services.calls == []

    services.available["mobile_app_test"] = object()
    second = HeimaEvent(
        type="debug.second",
        key="debug.second",
        severity="info",
        title="second",
        message="second",
    )
    await pipeline.async_emit(
        second,
        routes=[],
        dedup_window_s=0,
        rate_limit_per_key_s=0,
    )

    notify_calls = [c for c in services.calls if c[0] == "notify" and c[1] == "mobile_app_test"]
    assert len(notify_calls) == 1
    assert notify_calls[0][2]["title"] == "first"
    assert pipeline.stats.notify_route_retried == 1


@pytest.mark.asyncio
async def test_event_pipeline_resolves_recipient_aliases_and_groups():
    bus = _FakeBus()
    services = _FakeServices(
        available={
            "mobile_app_phone_stefano": object(),
            "mobile_app_mac_stefano": object(),
            "mobile_app_laura": object(),
            "mobile_app_legacy": object(),
        }
    )
    hass = SimpleNamespace(bus=bus, services=services)
    pipeline = HeimaEventPipeline(hass)

    await pipeline.async_emit(
        HeimaEvent(
            type="debug.targets",
            key="debug.targets",
            severity="info",
            title="Targets",
            message="targets",
        ),
        routes=["mobile_app_legacy"],
        recipients={
            "stefano": ["mobile_app_phone_stefano", "mobile_app_mac_stefano"],
            "laura": ["mobile_app_laura"],
        },
        recipient_groups={"family": ["stefano", "laura"]},
        route_targets=["family", "stefano", "missing"],
        dedup_window_s=0,
        rate_limit_per_key_s=0,
    )

    called_services = [service for domain, service, _data, _blocking in services.calls if domain == "notify"]
    assert called_services == [
        "mobile_app_legacy",
        "mobile_app_phone_stefano",
        "mobile_app_mac_stefano",
        "mobile_app_laura",
    ]
    assert pipeline.stats.notify_target_resolution_errors == 1
