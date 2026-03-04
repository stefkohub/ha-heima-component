# Heima — Options Flow SPEC v1
## Configuration & UX Schema (Product-Grade)

This document defines the **Options Flow schema** for the Heima integration.
It specifies UI steps, fields, validation rules, defaults, and runtime effects.

---

## Design Principles

- Incremental configuration (minimal viable setup first)
- Strong validation (no free-text entity_id)
- Deterministic effects (no hidden side effects)
- Restart-safe and non-destructive
- Backward compatible with config entry migrations

---

## Options Flow Overview

```
Heima Options
 ├─ General
 ├─ People
 │   ├─ Named persons
 │   └─ Anonymous presence
 ├─ Rooms (Occupancy)
 ├─ Lighting
 │   ├─ Rooms → Scenes
 │   └─ Zones
 ├─ Heating
 ├─ Security
 └─ Notifications
```

Each step is independently editable after initial setup.

---

## 1. General

### Fields
- `engine_enabled` (bool, default: true)
- `timezone` (string, default: HA timezone)
- `language` (string, default: HA language)
- `lighting_apply_mode` (enum: `scene`, `delegate`)

Optional house-signal bindings:
- `vacation_mode_entity` (entity picker: `input_boolean|binary_sensor|sensor`)
- `guest_mode_entity` (entity picker: `input_boolean|binary_sensor|sensor`)
- `sleep_window_entity` (entity picker: `input_boolean|binary_sensor|sensor`)
- `relax_mode_entity` (entity picker: `input_boolean|binary_sensor|sensor`)
- `work_window_entity` (entity picker: `input_boolean|binary_sensor|sensor`)

### Validation
- timezone must be valid IANA TZ
- language must be supported by HA

### Runtime Effect
- disabling engine blocks all apply phases but keeps canonical state updates
- stores the configurable house-signal bindings used by canonical house-state resolution

---

## 2. People — Named Persons

### Add / Edit Person

Fields:
- `slug` (string, required, immutable)
- `display_name` (string)
- `presence_method` (enum: `ha_person`, `quorum`, `manual`)

If `ha_person`:
- `person_entity` (entity picker: domain `person`)

If `quorum`:
- `sources` (multi-entity picker)
- `required` (int, 1..N)

Tuning (all methods):
- `arrive_hold_s` (int, default 10)
- `leave_hold_s` (int, default 120)

Optional:
- `enable_override` (bool)

### Validation
- slug unique
- entity exists and domain matches
- quorum.required <= len(sources)

### Runtime Effect
- updates PeopleAdapter
- recompute canonical person state

---

## 3. People — Anonymous Presence

Fields:
- `enabled` (bool)
- `sources` (multi-entity picker)
- `required` (int, default 1)
- `anonymous_count_weight` (int, default 1)
- `arrive_hold_s` (int, default 10)
- `leave_hold_s` (int, default 120)

Validation:
- required <= len(sources)

Runtime Effect:
- updates anonymous presence adapter
- affects `anyone_home`, `people_count`, `house_state`

---

## 4. Rooms (Occupancy)

### Add / Edit Room

Fields:
- `room_id` (slug, immutable)
- `display_name`
- `area_id` (HA area picker, optional but recommended for actuation fallback)
- `occupancy_mode` (enum: `derived`, `none`; default `derived`)
- `sources` (multi-entity picker, conditional)
- `logic` (enum: `any_of`, `all_of`, conditional)
- `on_dwell_s` (int, default 5)
- `off_dwell_s` (int, default 120)
- `max_on_s` (int, optional)

Validation:
- if `occupancy_mode = derived`: at least one source and `logic` required
- if `occupancy_mode = none`: `sources` may be empty and `logic` is ignored
- dwell values >= 0

Runtime Effect:
- updates room actuation + occupancy metadata
- recompute room occupancy only for `occupancy_mode = derived`

---

## 5. Lighting — Rooms → Scenes

### Per Room Mapping

Fields:
- `room_id` (from Rooms)
- `scene_evening` (scene picker or empty)
- `scene_relax` (scene picker or empty)
- `scene_night` (scene picker or empty)
- `scene_off` (scene picker or empty)

Optional:
- `enable_manual_hold` (bool, default true)

Validation:
- scenes must exist
- all scenes optional (room may rely on partial mapping or runtime fallback)

Runtime Effect:
- used by orchestrator for per-room apply
- creates `binary_sensor.heima_lighting_manual_hold_<room>`
- when intent is `off` and `scene_off` is empty, runtime may fallback to `light.turn_off` using the room `area_id`

---

## 6. Lighting — Zones

### Add / Edit Zone

Fields:
- `zone_id` (slug)
- `display_name`
- `rooms` (multi-select from rooms)
- `intent_entity` (auto-created select)

Validation:
- at least one room

Runtime Effect:
- lighting policy runs per-zone
- apply decomposed per-room
- zone occupancy ignores rooms with `occupancy_mode = none`
- zone with only `occupancy_mode = none` rooms resolves `zone_occupied = false` in `auto`

---

## 7. Heating

Fields:
### 7.1 Heating — General

Fields:
- `climate_entity` (entity picker: domain `climate`, required)
- `apply_mode` (enum: `delegate_to_scheduler`, `set_temperature`; default `delegate_to_scheduler`)
- `temperature_step` (float, required, > 0)
- `manual_override_guard` (bool, default `true`)

Optional external bindings:
- `outdoor_temperature_entity` (entity picker: domain `sensor`)
- `vacation_hours_from_start_entity` (entity picker: domain `sensor`)
- `vacation_hours_to_end_entity` (entity picker: domain `sensor`)
- `vacation_total_hours_entity` (entity picker: domain `sensor`)
- `vacation_is_long_entity` (entity picker: domain `binary_sensor`)

Validation:
- climate entity must exist and be `climate.*`
- `temperature_step > 0`
- helper bindings must match allowed domains

Runtime Effect:
- defines the Heating domain device binding
- defines shared apply-guard parameters
- provides external timing/weather inputs for built-in override branches

### 7.2 Heating — Override Branches

Heating v1 exposes a fixed mapping:

- `house_state -> built-in branch config`

All canonical `house_state` values are configurable:
- `away`
- `home`
- `guest`
- `vacation`
- `sleeping`
- `relax`
- `working`

Default for every state:
- `branch = disabled`

### 7.3 Heating — Branch Editor Flow

Recommended UI shape:

1. `Heating General`
2. `Heating Override Branches Menu`
3. select one `house_state`
4. edit its branch config
5. save and return to the branch menu

This mirrors the existing Heima edit-menu pattern and avoids one oversized form.

### 7.4 Heating — Per-State Branch Form

Common fields:
- `house_state` (selected from canonical values, immutable in edit)
- `branch` (enum: `disabled`, `scheduler_delegate`, `fixed_target`, `vacation_curve`)

#### If `branch = disabled`
- no additional fields

#### If `branch = scheduler_delegate`
- no additional fields

#### If `branch = fixed_target`
- `target_temperature` (float, required, > 0)

#### If `branch = vacation_curve`
- `vacation_ramp_down_h` (float, required, >= 0)
- `vacation_ramp_up_h` (float, required, >= 0)
- `vacation_min_temp` (float, required, > 0)
- `vacation_comfort_temp` (float, required, > 0)
  - semantic meaning: return preheat target before control is handed back to the external scheduler
- `vacation_min_total_hours_for_ramp` (float, required, >= 0)

### 7.5 Heating — Validation Rules

General:
- exactly one branch config per canonical `house_state`
- if a state has no stored config, effective branch = `disabled`

Branch-specific:
- `disabled` / `scheduler_delegate`:
  - no extra branch fields allowed
- `fixed_target`:
  - `target_temperature` required and > 0
- `vacation_curve`:
  - all vacation fields required
  - all temperatures > 0
  - hour-based values >= 0
  - no user-configured start temperature field:
    - branch start temperature is captured at runtime from the thermostat when the branch activates

Cross-checks:
- if any branch uses `vacation_curve`, the relevant timing bindings should be present in `Heating General`
  - `vacation_hours_from_start_entity`
  - `vacation_hours_to_end_entity`
  - `vacation_total_hours_entity`
  - `vacation_is_long_entity`
- if any branch uses `vacation_curve`, `outdoor_temperature_entity` is strongly recommended and may be required by the implementation

### 7.6 Heating — Persistence Shape

Conceptual stored shape:

```yaml
heating:
  climate_entity: climate.termostato
  apply_mode: delegate_to_scheduler
  temperature_step: 0.5
  manual_override_guard: true
  outdoor_temperature_entity: sensor.outdoor_temp
  vacation_hours_from_start_entity: sensor.heating_vacation_hours_from_start
  vacation_hours_to_end_entity: sensor.heating_vacation_hours_to_end
  vacation_total_hours_entity: sensor.heating_vacation_total_hours
  vacation_is_long_entity: binary_sensor.heating_vacation_is_long
  override_branches:
    vacation:
      branch: vacation_curve
      vacation_ramp_down_h: 8
      vacation_ramp_up_h: 10
      vacation_min_temp: 16.5
      vacation_comfort_temp: 19.5
      vacation_min_total_hours_for_ramp: 24
    sleeping:
      branch: fixed_target
      target_temperature: 17.5
    guest:
      branch: scheduler_delegate
```

### 7.7 Heating — Runtime Effect

- `Heating General` config binds the device and common external inputs
- `override_branches` drives the built-in fixed policy tree:
  - if current `house_state` matches a configured built-in branch, that branch is used
  - otherwise Heating falls back to the normal scheduler-following branch
- no policy plugins are involved in v1

---

## 8. Security (Read-Only)

Fields:
- `enabled` (bool)
- `security_state_entity` (entity picker)
- `armed_away_value` (string)
- `armed_home_value` (string)

Runtime Effect:
- consistency checks
- emits notification events only

---

## 9. Notifications

Fields:
- `routes` (list of notify services)
- `recipients` (logical alias mapping, `recipient_id -> list[notify.*]`)
- `recipient_groups` (logical groups, `group_id -> list[recipient_id]`)
- `route_targets` (list of logical notification targets: recipient ids or group ids)
- `enabled_event_categories` (multi-select: `people`, `occupancy`, `house_state`, `lighting`, `heating`, `security`; `system` always enabled)
- `dedup_window_s` (int, default 60)
- `rate_limit_per_key_s` (int, default 300)
- `occupancy_mismatch_policy` (`off|smart|strict`, default `smart`)
- `occupancy_mismatch_min_derived_rooms` (int, default `2`)
- `occupancy_mismatch_persist_s` (int, default `600`)
- `security_mismatch_policy` (`off|smart|strict`, default `smart`)
- `security_mismatch_persist_s` (int, default `300`)

Runtime Effect:
- affects notification policy and orchestrator
- route delivery resolves legacy `routes` plus logical `route_targets` through configured recipients/groups
- category toggles gate event emission before routing/dedup pipeline
- occupancy mismatch policy reduces false positives in partial-room-sensing homes
- security mismatch policy delays/suppresses `armed_away_but_home` false positives caused by stale trackers

---

## 10. Apply & Reload Semantics

- Option changes trigger re-evaluation
- No immediate mass-apply unless intent changes
- Safety rules always enforced

---

## 11. Migration Rules

- New fields get defaults
- Removed fields are ignored but preserved
- Major changes require migration step

---
