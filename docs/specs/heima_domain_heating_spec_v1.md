# Heima — Heating Domain Mini-SPEC v1

## Purpose

Define the first real Heating domain for Heima.

This v1 design is intentionally **not policy-pluggable yet**:
- Heating has a fixed internal policy tree.
- `house_state` may activate an explicit built-in **override policy branch**.
- Future pluggable policies remain a separate later evolution for all domains.

---

## 1. Domain Goal

Heating should:
- normally follow a scheduler / delegated schedule source
- safely apply setpoint changes when Heima is allowed to control the thermostat
- switch to a built-in **house-state override branch** when configured for the current `house_state`

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
2. else if the current `house_state` maps to a configured built-in override branch -> use that branch
3. else -> use `normal` policy branch

`normal` policy branch:
- default behavior is scheduler-following
- if `apply_mode = delegate_to_scheduler`, Heima emits an intent but does not override the setpoint
- if `apply_mode = set_temperature`, future non-vacation logic may set a target, but this is out of scope for the first mini-spec

Override policy branch:
- temporarily overrides the normal branch
- is selected from a fixed built-in branch catalog keyed by `house_state`

Built-in branch catalog for v1:
- `disabled`
- `scheduler_delegate`
- `fixed_target`
- `vacation_curve`

Meaning:
- `disabled`
  - no override branch is active for that `house_state`
- `scheduler_delegate`
  - explicitly preserve scheduler ownership in that state
- `fixed_target`
  - Heima targets a fixed configured setpoint in that state
- `vacation_curve`
  - Heima computes a time-based vacation temperature curve

This explicit branching is contractual in v1.

---

## 4. Required Inputs

### 4.1 Core Device Binding

- `climate_entity` (required)

### 4.1.1 Core Heating Config

Heating v1 requires a configuration model that can represent:

- `climate_entity`
- `apply_mode`
- `temperature_step`
- `manual_override_guard`

Recommended v1 defaults:
- `apply_mode = delegate_to_scheduler`
- `temperature_step` read from config first, device-derived later if supported
- `manual_override_guard = enabled`

### 4.2 Override Branch Inputs

All override branches depend on:

- `house_state` (must already be canonicalized by Heima)
- `current_setpoint` (read from the `climate` entity)
- `manual_override_state` (derived from thermostat state / preset / hold)

Additional inputs depend on the selected branch type.

### 4.2.1 House-State Branch Mapping (Config Model)

Heating v1 must support an explicit configuration mapping:

- `house_state -> built-in branch type`

Recommended shape:

- `override_branches`
  - keyed by canonical `house_state`
  - value = branch config object

Conceptual example:

```yaml
override_branches:
  vacation:
    branch: vacation_curve
  sleeping:
    branch: fixed_target
    target_temperature: 17.5
  guest:
    branch: scheduler_delegate
```

Rules:
- only canonical Heima `house_state` values are valid keys
- if a state is not configured, effective branch = `disabled`
- exactly one branch object per `house_state`
- branch objects are validated according to branch type

This mapping is fixed-policy configuration, not a plugin registry.

### 4.3 Vacation Curve Inputs

The v1 domain consumes the following inputs for the vacation branch:

- `outdoor_temperature`

Vacation timing context:
- `hours_from_start`
- `hours_to_end`
- `total_hours`
- `is_long`

These may initially come from bound helper/sensor entities.
In v1 they do **not** need to be natively modeled by Heima yet.

### 4.4 Fixed Target Branch Inputs

For a `fixed_target` branch, v1 requires:

- `target_temperature`

Validation:
- `target_temperature` must be > 0
- runtime must quantize it using `temperature_step`

### 4.5 Vacation Policy Parameters

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

### 4.6 Vacation Branch Config Shape

Recommended branch-specific configuration:

```yaml
override_branches:
  vacation:
    branch: vacation_curve
    vacation_ramp_down_h: 8
    vacation_ramp_up_h: 10
    vacation_min_temp: 16.5
    vacation_comfort_temp: 19.5
    vacation_start_temp: 19.5
    vacation_min_total_hours_for_ramp: 24
```

Rules:
- all vacation parameters belong to the `vacation_curve` branch config
- these values are branch-local, not global heating values
- if a branch is not `vacation_curve`, these fields are invalid and must be rejected or dropped

This keeps the configuration aligned with the branch-selector model.

### 4.7 External Sensor Bindings (v1 pragmatic path)

For the first implementation, the following may be modeled as explicit optional bindings in Heating config:

- `outdoor_temperature_entity`
- `vacation_hours_from_start_entity`
- `vacation_hours_to_end_entity`
- `vacation_total_hours_entity`
- `vacation_is_long_entity`

This allows Heima to consume existing HA helpers/sensors without introducing native vacation-window modeling yet.

---

## 5. House-State Override Branches

### 5.1 Branch Selection

For the current canonical `house_state`, Heating may activate one built-in override branch.

Recommended v1 semantics:
- at most one branch is active at a time
- because `house_state` is singular, there is no branch conflict resolution in v1

Examples:
- `vacation -> vacation_curve`
- `sleeping -> fixed_target`
- `guest -> scheduler_delegate`
- any unmapped state -> `normal`

### 5.2 `scheduler_delegate` Branch

Semantics:
- explicitly preserve scheduler ownership
- do not send a thermostat setpoint override
- still expose diagnostics showing that an override branch was matched but delegated

### 5.3 `fixed_target` Branch

Semantics:
- compute `target = configured fixed target`
- quantize using the same thermostat step rules as other active-setpoint branches
- apply through the same guard layer

This branch is intentionally simple and reusable for states such as:
- `sleeping`
- `away`
- `working`

### 5.4 `vacation_curve` Branch

The `vacation_curve` branch is the first full built-in algorithmic branch.

## 6. Vacation Curve Policy

### 6.1 Activation

The vacation curve branch is active when:
- the override branch selector resolves the current `house_state` to `vacation_curve`

### 6.2 Phases

The policy has four phases:

- `eco_only`
- `ramp_down`
- `cruise`
- `ramp_up`

### 6.3 Phase Selection

If `is_long = false`:
- phase = `eco_only`
- target uses the safety-adjusted eco temperature only

If `is_long = true`:
- if `hours_from_start < vacation_ramp_down_h`: phase = `ramp_down`
- else if `hours_to_end < vacation_ramp_up_h`: phase = `ramp_up`
- else: phase = `cruise`

### 6.4 Safety-Adjusted Eco Temperature

`t_min_safety`:
- `max(vacation_min_temp, 17.0)` when `outdoor_temperature <= 0`
- `max(vacation_min_temp, 16.5)` when `outdoor_temperature <= 3`
- `vacation_min_temp` otherwise

### 6.5 Raw Target Calculation

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

### 6.6 Quantization

The computed target is quantized to the thermostat step:

- `target = round(t_raw / temperature_step) * temperature_step`

This is required to avoid impossible or noisy setpoints.

---

## 7. Apply Guard Rules

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

This is the contractual first apply behavior for active setpoint branches (`fixed_target` and `vacation_curve`).

### 6.4 Rate Limiting

Heating apply must be rate-limited to avoid thermostat spam.

The first mini-spec does not hardcode the exact interval, but the implementation must include:
- at least one minimum interval guard
- idempotent skip when target is unchanged

---

## 8. Canonical Heating Outputs

Heating v1 should expose at least:

- `sensor.heima_heating_state`
  - e.g. `idle`, `scheduler`, `vacation_override`, `manual_blocked`

- `sensor.heima_heating_reason`
  - e.g. `normal_scheduler`, `vacation_curve`, `manual_override`, `small_delta_skip`

- `sensor.heima_heating_phase`
  - `eco_only`, `ramp_down`, `cruise`, `ramp_up`, or empty when not in `vacation_curve`

- `sensor.heima_heating_target_temp`
  - the currently computed target setpoint, when applicable (`fixed_target` or `vacation_curve`)

- `select.heima_heating_intent`
  - future-facing canonical intent selector (`auto`, `eco`, `comfort`, `preheat`, `off`)
  - for v1, `auto` is sufficient as the normal default

These names are subject to the existing entity registry conventions in the main Heima spec.

---

## 9. Diagnostics

Heating diagnostics must expose at least:

- active branch:
  - `normal`
  - `scheduler_delegate`
  - `fixed_target`
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

For `fixed_target`, diagnostics may omit vacation timing fields but must still expose:
- branch
- configured target
- quantized target
- current setpoint
- delta

This is required to replace ad hoc logbook-style debugging with structured diagnostics.

---

## 10. Events (initial set)

Recommended initial events for Heating v1:

- `heating.vacation_phase_changed`
- `heating.target_changed`
- `heating.apply_skipped_small_delta`
- `heating.manual_override_blocked`

These should follow the existing Heima event pipeline and category gating rules.

---

## 11. Relationship with the Normalization Layer

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

## 12. Relationship with Future Policy Plugins

The built-in branch selector introduced here is designed to evolve cleanly into the future Policy Plugin Framework.

Expected future evolution:
- current mapping:
  - `house_state -> built-in branch type`
- future mapping:
  - `house_state -> policy plugin / built-in policy id`

This means the v1 selector is intentionally compatible with future policy pluggability without requiring it now.

---

## 13. Future Evolution (Not in v1)

Future versions may add:

- policy-pluggable Heating strategies
- native Heima vacation window modeling (instead of helper-bound timing sensors)
- configurable safety-temperature bands
- richer non-vacation temperature policies

When policy pluggability is introduced, the current `vacation_curve` branch should become a first built-in Heating policy implementation.
