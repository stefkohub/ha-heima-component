# Project Decisions

This file records temporary or transitional product/architecture decisions that are intentional today and expected to be revisited later.

## 2026-03-04 — Notification routes are retained as legacy fallback

Decision:
- keep `notifications.routes` in v1
- do **not** deprecate it yet

Reason:
- the new notification recipient alias/group model is being introduced incrementally
- existing installations already use flat `notify.*` routes
- immediate deprecation would add migration friction for little short-term value

Current rule:
- `routes` remains supported as a legacy fallback transport list
- recipient aliases/groups are the preferred direction for new configuration

Future follow-up:
- deprecate `routes` after:
  - recipient aliases/groups are stable in real use
  - migration semantics are defined
  - UI/runtime coverage for logical routing is complete

## 2026-03-04 — Heating v1 does not implement retry/verify loops

Decision:
- keep Heating apply in v1 limited to guarded `climate.set_temperature` requests
- do **not** implement thermostat verify/retry logic inside Heima for now

Reason:
- Home Assistant and the thermostat integration remain the right place for transport/device retry behavior at this stage
- adding a retry layer now would increase complexity and blur responsibility boundaries

Current rule:
- Heating uses:
  - small-delta skip
  - rate limit
  - idempotence
  - startup/service-race tolerance
- but no post-apply verification or retry loop

Future follow-up:
- revisit only as an optional enhancement if real-world device behavior proves it necessary

## 2026-03-04 — `scheduler_delegate` means Heima yields to the external scheduler

Decision:
- treat `scheduler_delegate` as a passive handoff mode

Reason:
- Heima does not yet integrate with external scheduler internals or future setpoints
- the clean v1 behavior is to stop writing thermostat targets and let the external scheduler own control

Current rule:
- when Heating selects `scheduler_delegate`, Heima:
  - reports delegated state
  - does not push thermostat setpoints

Future follow-up:
- explicit scheduler integration can be added later if a real contract is defined

## 2026-03-04 — Heating remains fixed-policy in v1 (not policy-pluggable yet)

Decision:
- keep Heating on a fixed built-in policy tree in v1
- do **not** implement policy plugins in runtime yet

Reason:
- Heating needed a stable MVP first
- introducing pluggable domain policies now would add more abstraction before the base domain proves itself in real use

Current rule:
- built-in branch catalog:
  - `disabled`
  - `scheduler_delegate`
  - `fixed_target`
  - `vacation_curve`

Future follow-up:
- first planned adopter of the future Policy Plugin Framework is Heating, starting with `vacation_curve`

## 2026-03-04 — House-state signals are configurable, not hardcoded helpers

Decision:
- remove hardcoded helper assumptions for house-state side signals

Reason:
- hardcoded entities like `binary_sensor.work_window` were not guaranteed to exist
- this made `house_state` behavior fragile and environment-dependent

Current rule:
- `vacation_mode`
- `guest_mode`
- `sleep_window`
- `relax_mode`
- `work_window`
are read only from configured `house_signals` bindings
- missing bindings are treated as `off`

Future follow-up:
- none required for the model itself; only UX refinements if needed

## 2026-03-04 — `heima.set_mode` is a final runtime-only house-state override

Decision:
- define `heima.set_mode` as a final `house_state` override, not a boolean mode-signal setter

Reason:
- the service name implies “set the house mode/state”, not “toggle one input signal”
- using it for signal toggles would make the API misleading

Current rule:
- `state=true`:
  - set the singular runtime override to the requested canonical state
- `state=false`:
  - clear it only if the current override matches that same state
- the override is runtime-only and cleared on reload/restart

Future follow-up:
- persistent overrides are possible later, but not part of v1

## 2026-03-04 — `vacation_curve` captures start temperature at activation

Decision:
- remove configured `vacation_start_temp`
- capture the curve start temperature from the thermostat when the branch becomes active

Reason:
- a fixed configured start value can drift from the real thermostat state
- the correct ramp-down origin is the actual active setpoint when vacation control starts

Current rule:
- `vacation_curve` stores the start temperature at branch activation
- it reuses that captured value until the branch exits

Future follow-up:
- optional fallback behavior can be added only if a thermostat current setpoint is unavailable

## 2026-03-04 — `vacation_comfort_temp` is a return preheat target, not the post-vacation truth

Decision:
- keep `vacation_comfort_temp`, but treat it as a preheat target before scheduler handoff

Reason:
- the external scheduler may want a different target at the exact end of vacation
- Heima does not yet know the scheduler’s future setpoint

Current rule:
- ramp-up aims toward a return preheat target
- at vacation end, control returns to `scheduler_delegate`
- the external scheduler may immediately apply a different target

Future follow-up:
- if Heima later knows the scheduler return target, `vacation_curve` can ramp toward that instead

## 2026-03-04 — Runtime timing is centralized in the shared scheduler

Decision:
- use the shared Runtime Scheduler as the single timing substrate for internal delayed/deadline-based behavior

Reason:
- ad hoc timers across domains would fragment timing logic and make cleanup/diagnostics harder

Current rule:
- occupancy dwell and max-on
- occupancy mismatch persistence
- security mismatch persistence
- Heating `vacation_curve` timed rechecks
all schedule through the shared runtime scheduler

Future follow-up:
- future timed domains/features (e.g. Watering, policy plugins) must reuse this scheduler instead of introducing custom timer paths

## 2026-03-04 — Normalization plugins and policy plugins remain distinct layers

Decision:
- keep the normalization plugin framework separate from the future policy plugin framework

Reason:
- normalization plugins combine and normalize signals
- policy plugins change domain decisions
- mixing the two would make the architecture ambiguous

Current rule:
- normalization plugins are active and used in runtime
- policy plugins are specified only, not implemented yet

Future follow-up:
- implement the policy plugin framework as a distinct runtime subsystem, with Heating as the first planned real adopter
