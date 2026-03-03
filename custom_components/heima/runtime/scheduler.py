"""Shared internal runtime scheduler for Heima timed rechecks."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later


@dataclass(frozen=True)
class ScheduledRuntimeJob:
    """A single keyed runtime job scheduled against the HA loop."""

    job_id: str
    owner: str
    entry_id: str
    due_monotonic: float
    label: str


class RuntimeScheduler:
    """Keyed internal scheduler for runtime delayed/deadline rechecks."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        entry_id: str,
        on_job_due: Callable[[str], Awaitable[None]],
    ) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._on_job_due = on_job_due
        self._jobs: dict[str, ScheduledRuntimeJob] = {}
        self._unsubs: dict[str, Callable[[], None]] = {}
        self._last_fired_at: dict[str, float] = {}

    def sync_jobs(self, jobs: dict[str, ScheduledRuntimeJob]) -> None:
        """Reconcile pending jobs with the desired keyed schedule set."""
        desired = dict(jobs)

        for job_id in list(self._jobs):
            if job_id not in desired:
                self.cancel(job_id)

        for job_id, job in desired.items():
            current = self._jobs.get(job_id)
            if current == job and job_id in self._unsubs:
                continue
            self._schedule(job)

    def cancel(self, job_id: str) -> None:
        unsub = self._unsubs.pop(job_id, None)
        if unsub:
            unsub()
        self._jobs.pop(job_id, None)

    def cancel_owner(self, *, owner: str | None = None) -> None:
        for job_id, job in list(self._jobs.items()):
            if owner is not None and job.owner != owner:
                continue
            self.cancel(job_id)

    async def async_shutdown(self) -> None:
        self.cancel_owner()

    def diagnostics(self) -> dict[str, object]:
        now = time.monotonic()
        pending = []
        for job in self._jobs.values():
            pending.append(
                {
                    "job_id": job.job_id,
                    "owner": job.owner,
                    "entry_id": job.entry_id,
                    "label": job.label,
                    "due_in_s": max(0.0, job.due_monotonic - now),
                    "due_monotonic": job.due_monotonic,
                    "last_fired_at_monotonic": self._last_fired_at.get(job.job_id),
                }
            )
        pending.sort(key=lambda item: item["due_monotonic"])
        return {"pending_jobs": pending}

    def _schedule(self, job: ScheduledRuntimeJob) -> None:
        self.cancel(job.job_id)
        self._jobs[job.job_id] = job
        delay = max(0.1, job.due_monotonic - time.monotonic())

        @callback
        def _handle_due(_now) -> None:
            self._unsubs.pop(job.job_id, None)
            self._jobs.pop(job.job_id, None)
            self._last_fired_at[job.job_id] = time.monotonic()
            self._hass.async_create_task(self._on_job_due(job.job_id))

        self._unsubs[job.job_id] = async_call_later(self._hass, delay, _handle_due)
