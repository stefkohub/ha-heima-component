# Heima — Heating Domain Mini-SPEC v1

## Purpose

Define the first real Heating domain for Heima.

This v1 design is intentionally **not policy-pluggable yet**:
- Heating has a fixed internal policy tree.
- `vacation` is modeled as an explicit **override policy branch**.
- Future pluggable policies remain a separate later evolution for all domains.

---

## 1. Domain Goal

Heating should:
- normally follow a scheduler / delegated schedule source
- safely apply setpoint changes when Heima is allowed to control the thermostat
- switch to a dedicated **vacation curve override** when `house_state = vacation`

The Heating domain is responsible for:
- deciding the effective heating intent
- deciding the target temperature when Heima is actively controlling
- guarding apply (manual override, small delta, rate limiting)
- exposing diagnostics and standard events

---

## 2. Non-Goals (v1)

Heating v1 does **not** include:
- policy plugins
- multi-zone heating orchestration
- adaptive learning / self-tuning
- weather-compensated PID-like control
- advanced per-room radiator balancing

Those can be added later once the fixed policy tree is stable.

---

## 3. Core Model

### 3.1 Apply Modes

Heating supports two apply modes:

- `delegate_to_scheduler`
  - Heima does not push setpoints in normal mode
  - a scheduler (native thermostat schedule / external scheduler) remains the owner

- `set_temperature`
  - Heima actively sets the target temperature on the bound `climate` entity

### 3.2 Policy Tree (fixed in v1)

The Heating policy tree is:

1. if manual override guard blocks control -> no active apply
2. else if `house_state = vacation` -> use `vacation_curve` policy branch
3. else -> use `normal` policy branch

`normal` policy branch:
- default behavior is scheduler-following
- if `apply_mode = delegate_to_scheduler`, Heima emits an intent but does not override the setpoint
- if `apply_mode = set_temperature`, future non-vacation logic may set a target, but this is out of scope for the first mini-spec

`vacation_curve` policy branch:
- temporarily overrides the normal branch
- computes a target setpoint from vacation timing and weather safety rules

This explicit branching is contractual in v1.

---

## 4. Required Inputs

### 4.1 Core Device Binding

- `climate_entity` (required)

### 4.2 Vacation Curve Inputs

The v1 domain consumes the following inputs for the vacation branch:

- `house_state` (must already be canonicalized by Heima)
- `outdoor_temperature`
- `current_setpoint` (read from the `climate` entity)
- `manual_override_state` (derived from thermostat state / preset / hold)

Vacation timing context:
- `hours_from_start`
- `hours_to_end`
- `total_hours`
- `is_long`

These may initially come from bound helper/sensor entities.
In v1 they do **not** need to be natively modeled by Heima yet.

### 4.3 Vacation Policy Parameters

- `temperature_step`
- `vacation_ramp_down_h`
- `vacation_ramp_up_h`
- `vacation_min_temp`
- `vacation_comfort_temp`
- `vacation_start_temp`
- `vacation_min_total_hours_for_ramp`

Outdoor safety thresholds (v1 default fixed logic):
- if `outdoor_temperature <= 0`: eco floor must be at least `17.0`
- else if `outdoor_temperature <= 3`: eco floor must be at least `16.5`
- else: eco floor is `vacation_min_temp`

These thresholds may become configurable later, but v1 allows fixed defaults.

---

## 5. Vacation Curve Policy

### 5.1 Activation

The vacation branch is active when:
- `house_state = vacation`

### 5.2 Phases

The policy has four phases:

- `eco_only`
- `ramp_down`
- `cruise`
- `ramp_up`

### 5.3 Phase Selection

If `is_long = false`:
- phase = `eco_only`
- target uses the safety-adjusted eco temperature only

If `is_long = true`:
- if `hours_from_start < vacation_ramp_down_h`: phase = `ramp_down`
- else if `hours_to_end < vacation_ramp_up_h`: phase = `ramp_up`
- else: phase = `cruise`

### 5.4 Safety-Adjusted Eco Temperature

`t_min_safety`:
- `max(vacation_min_temp, 17.0)` when `outdoor_temperature <= 0`
- `max(vacation_min_temp, 16.5)` when `outdoor_temperature <= 3`
- `vacation_min_temp` otherwise

### 5.5 Raw Target Calculation

If phase = `eco_only`:
- `t_raw = t_min_safety`

If phase = `cruise`:
- `t_raw = t_min_safety`

If phase = `ramp_down`:
- linear interpolation from `vacation_start_temp` to `t_min_safety`
- ratio = `hours_from_start / vacation_ramp_down_h`

If phase = `ramp_up`:
- linear interpolation from `t_min_safety` to `vacation_comfort_temp`
- ratio = `1 - (hours_to_end / vacation_ramp_up_h)`

If timing data is invalid (`total_hours <= 0`):
- fail safe to `t_min_safety`

### 5.6 Quantization

The computed target is quantized to the thermostat step:

- `target = round(t_raw / temperature_step) * temperature_step`

This is required to avoid impossible or noisy setpoints.

---

## 6. Apply Guard Rules

Before applying a setpoint:

### 6.1 Manual Override Guard

If the thermostat indicates a manual/preset state that must not be overridden:
- no apply is sent
- diagnostics must record the block reason
- an event may be emitted (`heating.manual_override_blocked`)

### 6.2 Small Delta Guard

Heima only applies a new target if:
- `abs(target - current_setpoint) >= temperature_step`

If not:
- skip apply
- diagnostics should record `small_delta_skip`

### 6.3 Apply Command

When apply is allowed:
- `hvac_mode = heat`
- `temperature = target`

This is the contractual first apply behavior for the vacation branch.

### 6.4 Rate Limiting

Heating apply must be rate-limited to avoid thermostat spam.

The first mini-spec does not hardcode the exact interval, but the implementation must include:
- at least one minimum interval guard
- idempotent skip when target is unchanged

---

## 7. Canonical Heating Outputs

Heating v1 should expose at least:

- `sensor.heima_heating_state`
  - e.g. `idle`, `scheduler`, `vacation_override`, `manual_blocked`

- `sensor.heima_heating_reason`
  - e.g. `normal_scheduler`, `vacation_curve`, `manual_override`, `small_delta_skip`

- `sensor.heima_heating_phase`
  - `eco_only`, `ramp_down`, `cruise`, `ramp_up`, or empty when not in vacation branch

- `sensor.heima_heating_target_temp`
  - the currently computed target setpoint, when applicable

- `select.heima_heating_intent`
  - future-facing canonical intent selector (`auto`, `eco`, `comfort`, `preheat`, `off`)
  - for v1, `auto` is sufficient as the normal default

These names are subject to the existing entity registry conventions in the main Heima spec.

---

## 8. Diagnostics

Heating diagnostics must expose at least:

- active branch:
  - `normal`
  - `vacation_curve`

- current phase
- raw target (`t_raw`)
- quantized target
- current setpoint
- delta
- outdoor temperature
- safety-adjusted minimum
- `hours_from_start`
- `hours_to_end`
- `total_hours`
- `is_long`
- whether manual override blocked apply
- whether apply was skipped due to small delta

This is required to replace ad hoc logbook-style debugging with structured diagnostics.

---

## 9. Events (initial set)

Recommended initial events for Heating v1:

- `heating.vacation_phase_changed`
- `heating.target_changed`
- `heating.apply_skipped_small_delta`
- `heating.manual_override_blocked`

These should follow the existing Heima event pipeline and category gating rules.

---

## 10. Relationship with the Normalization Layer

The temperature-curve algorithm itself is **not** a signal-fusion plugin.

It remains explicit Heating policy logic.

The normalization/plugin infrastructure is still relevant for:
- control eligibility signals
- manual override corroboration
- safety inhibitions
- future heating gating logic (e.g. scheduler + window + occupancy + constraints)

This keeps concerns separated:
- signal fusion in the normalization layer
- setpoint policy in the Heating domain

---

## 11. Future Evolution (Not in v1)

Future versions may add:

- policy-pluggable Heating strategies
- native Heima vacation window modeling (instead of helper-bound timing sensors)
- configurable safety-temperature bands
- richer non-vacation temperature policies

When policy pluggability is introduced, the current `vacation_curve` branch should become a first built-in Heating policy implementation.
