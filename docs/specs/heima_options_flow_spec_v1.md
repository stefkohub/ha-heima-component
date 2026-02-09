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

### Validation
- timezone must be valid IANA TZ
- language must be supported by HA

### Runtime Effect
- disabling engine blocks all apply phases but keeps canonical state updates

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
- `sources` (multi-entity picker)
- `logic` (enum: `any_of`, `all_of`)
- `on_dwell_s` (int, default 5)
- `off_dwell_s` (int, default 120)
- `max_on_s` (int, optional)

Validation:
- at least one source
- dwell values >= 0

Runtime Effect:
- updates OccupancyAdapter
- recompute room occupancy

---

## 5. Lighting — Rooms → Scenes

### Per Room Mapping

Fields:
- `room_id` (from Rooms)
- `scene_evening` (scene picker)
- `scene_relax` (scene picker)
- `scene_night` (scene picker)
- `scene_off` (scene picker or empty)

Optional:
- `enable_manual_hold` (bool, default true)

Validation:
- scenes must exist
- at least one scene defined

Runtime Effect:
- used by orchestrator for per-room apply
- creates `binary_sensor.heima_lighting_manual_hold_<room>`

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

---

## 7. Heating

Fields:
- `climate_entity` (entity picker: domain `climate`)
- `apply_mode_auto` (enum: `delegate_to_scheduler`, `set_temperature`)
- `setpoint_eco` (float)
- `setpoint_comfort` (float)
- `setpoint_preheat` (float)

Safety:
- `min_seconds_between_commands` (int, default 120)
- `verify_after_s` (int, default 15)
- `max_retries` (int, default 2)

Validation:
- climate entity exists
- setpoints reasonable (range check)

Runtime Effect:
- updates HeatingOrchestrator parameters

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
- `dedup_window_s` (int, default 60)
- `rate_limit_per_key_s` (int, default 300)

Runtime Effect:
- affects notification policy and orchestrator

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
