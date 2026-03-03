# Heima Runtime Scheduler — Mini-SPEC v1

## 1. Purpose

This mini-spec defines a shared internal scheduler for time-driven runtime behavior in Heima.

The scheduler exists to prevent ad hoc timer logic from being spread across:
- occupancy dwell
- persistence-based mismatch checks
- Heating timed policy branches
- future Watering runtime
- future policy plugins that need time-based re-evaluation

The scheduler is a **core runtime facility**. It is not a domain feature.

---

## 2. Scope

The Runtime Scheduler is responsible for:
- registering keyed scheduled jobs
- cancelling/replacing jobs
- dispatching due jobs
- surfacing scheduler diagnostics

The Runtime Scheduler is **not** responsible for:
- domain policy logic
- signal fusion
- direct actuator calls
- replacing the Heima event pipeline

When a job fires, the scheduler must trigger a controlled runtime callback (typically a re-evaluation request), not arbitrary business logic embedded inside the scheduler.

---

## 3. Core Model

Each scheduled job must have at least:

- `job_id`
  - unique, stable within the owning config entry
- `owner`
  - logical subsystem name, e.g. `occupancy`, `heating`, `security`
- `entry_id`
  - the config entry that owns the job
- `trigger`
  - when the job becomes due
- `callback`
  - the function to run when due
- `label`
  - optional human-readable diagnostic label

### 3.1 Keyed Scheduling

Scheduling is keyed.

This means a caller may schedule a job again using the same `job_id`, and the scheduler must:
- replace the previous schedule
- avoid duplicate live timers for the same logical purpose

This is a contractual requirement in v1.

---

## 4. Trigger Types (v1)

The scheduler must support these trigger types:

### 4.1 Relative Delay

“Run after N seconds”.

Use cases:
- occupancy dwell transition checks
- persistence-based mismatch checks
- short deferred rechecks

### 4.2 Absolute Deadline

“Run at timestamp T”.

Use cases:
- Heating branch phase boundaries
- future Watering cycle milestones
- future policy deadlines

Only these two trigger types are required in v1.

The scheduler does not need recurring/cron semantics in v1.

---

## 5. Dispatch Semantics

When a job becomes due, the scheduler must:

1. mark the job as firing
2. remove or clear its pending schedule
3. invoke the registered callback
4. record a diagnostic fire timestamp

Callbacks should normally request a controlled re-evaluation, for example:
- `reason = "scheduler:heating:vacation_curve"`
- `reason = "scheduler:occupancy:room:studio"`

The scheduler should not contain domain policy branches. It only provides timing and dispatch.

---

## 6. Ownership and Cleanup

All jobs must be associated with a specific `entry_id`.

On:
- config entry reload
- config entry unload
- integration shutdown

the scheduler must cancel all jobs owned by that entry.

This is mandatory to avoid stale timers surviving config changes.

---

## 7. Diagnostics

The scheduler must expose enough diagnostics to debug timing behavior.

Minimum diagnostic fields:
- pending jobs
- `job_id`
- `owner`
- `entry_id`
- trigger type
- due time
- label
- last fired timestamp

Optional but recommended:
- schedule count
- reschedule count
- cancel count
- last callback error

Diagnostics must be visible through the main runtime diagnostics payload.

---

## 8. Failure Handling

If a scheduled callback fails:
- the scheduler must not crash the integration
- the error must be logged
- the failure must be surfaced in diagnostics

The scheduler itself must remain operational for other jobs.

This mirrors the fail-safe approach already used in:
- notification routing
- normalization plugin fallback

---

## 9. Integration with Existing Runtime

The current codebase already has a primitive timed recheck mechanism:
- engine computes a next deadline
- coordinator owns a timer and re-runs evaluation

The Runtime Scheduler v1 must replace that implicit mechanism with an explicit shared subsystem.

The migration path is:

1. move occupancy dwell rechecks onto the scheduler
2. move persistence-based mismatch rechecks onto the scheduler
3. move Heating timed branch rechecks onto the scheduler

This keeps migration incremental and low-risk.

---

## 10. Heating Requirements (v1)

Heating timed branches (especially `vacation_curve`) must not rely only on the refresh cadence of:
- `vacation_hours_from_start_entity`
- `vacation_hours_to_end_entity`
- `vacation_total_hours_entity`

When a timed Heating branch is active, Heima must schedule its own recheck.

### 10.1 Smart Next Check

The next Heating recheck should be computed intelligently, not with a blind tight polling loop.

At minimum, the scheduler must allow Heating to recheck at:
- the next phase boundary, or
- the next moment when the quantized target could change

If exact quantized-step timing is not yet implemented, a conservative bounded interval may be used temporarily, but the design target is event-driven deadline calculation.

---

## 11. API Shape (Conceptual)

The implementation may vary, but the core API should be conceptually equivalent to:

- `schedule_after(job_id, owner, entry_id, delay_s, callback, label=None)`
- `schedule_at(job_id, owner, entry_id, when_ts, callback, label=None)`
- `cancel(job_id, entry_id=None)`
- `cancel_owner(entry_id, owner=None)`
- `diagnostics()`

The exact method names are not contractual in v1.
The behavior is.

---

## 12. Future Evolution

This scheduler is expected to become the common timing substrate for:
- Heating timed policies
- Watering windows/cycles
- future policy plugins with deadlines
- future domain-level periodic checks

It remains separate from:
- the normalization plugin system
- the future policy plugin framework

Those systems may use the scheduler, but they do not replace it.

---

## 13. v1 Non-Goals

The Runtime Scheduler v1 does not need:
- cron syntax
- calendar rules
- arbitrary external event subscriptions
- distributed persistence across restarts
- user-facing scheduler UI

This is an internal runtime service only.

