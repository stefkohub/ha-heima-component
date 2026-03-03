from __future__ import annotations

import asyncio
import time

import pytest
from homeassistant.core import HomeAssistant

from custom_components.heima.runtime.scheduler import RuntimeScheduler, ScheduledRuntimeJob


@pytest.mark.asyncio
async def test_runtime_scheduler_replaces_keyed_job_and_fires_once(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    fired: list[str] = []

    async def _on_due(job_id: str) -> None:
        fired.append(job_id)

    scheduler = RuntimeScheduler(hass, entry_id="entry-1", on_job_due=_on_due)
    now = time.monotonic()

    scheduler.sync_jobs(
        {
            "job:test": ScheduledRuntimeJob(
                job_id="job:test",
                owner="test",
                entry_id="entry-1",
                due_monotonic=now + 0.4,
                label="Initial",
            )
        }
    )
    scheduler.sync_jobs(
        {
            "job:test": ScheduledRuntimeJob(
                job_id="job:test",
                owner="test",
                entry_id="entry-1",
                due_monotonic=now + 0.8,
                label="Replaced",
            )
        }
    )

    await asyncio.sleep(0.5)
    await hass.async_block_till_done()
    assert fired == []

    await asyncio.sleep(0.4)
    await hass.async_block_till_done()
    assert fired == ["job:test"]
    assert scheduler.diagnostics()["pending_jobs"] == []
