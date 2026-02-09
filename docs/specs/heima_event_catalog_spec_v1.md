# Heima — Event Catalog SPEC v1
## Notification Domain: Standard Events, Keys, Severity, Payloads

This document defines the **standard event catalog** emitted by Heima.
Events are consumed by the Notification domain to route messages via `notify.*`
with **deduplication** and **rate limiting**.

---

## 0. Event Model

### 0.1 Event Envelope (Canonical)
All events conform to this envelope:

- `event_id` (string, uuid-like)
- `ts` (ISO8601)
- `key` (string) — used for dedup/rate-limit
- `type` (string) — stable event type identifier
- `severity` (enum: `info | warn | crit`)
- `title` (string)
- `message` (string)
- `context` (object/dict) — redacted, safe to share in diagnostics

### 0.2 Dedup & Rate Limit Controls
Configured in Options Flow:
- `dedup_window_s` (default 60)
- `rate_limit_per_key_s` (default 300)

Rules:
- Events with same `key` within `dedup_window_s` are dropped
- Events with same `key` within `rate_limit_per_key_s` are suppressed (counted)

---

## 1. Naming Conventions

### 1.1 `type`
Use dot-separated stable identifiers:
- `people.*`
- `occupancy.*`
- `house_state.*`
- `lighting.*`
- `heating.*`
- `security.*`
- `system.*`

### 1.2 `key`
`key` must be stable and suitable for throttling.
Recommended patterns:
- `people.arrive.<person_slug>`
- `people.leave.<person_slug>`
- `security.mismatch`
- `heating.manual_override`
- `lighting.hold.<room_id>`

---

## 2. Standard Events (v1)

### 2.1 People

#### E001 — Named Person Arrived
- `type`: `people.arrive`
- `key`: `people.arrive.<person_slug>`
- `severity`: `info`
- `context`:
  - `person`: `<person_slug>`
  - `source`: `ha_person|quorum|manual`
  - `confidence`: int 0..100

#### E002 — Named Person Left
- `type`: `people.leave`
- `key`: `people.leave.<person_slug>`
- `severity`: `info`
- `context`:
  - `person`
  - `source`
  - `confidence`

#### E003 — Anonymous Presence Detected
- `type`: `people.anonymous_on`
- `key`: `people.anonymous`
- `severity`: `info`
- `context`:
  - `source`
  - `confidence`
  - `weight`: anonymous_count_weight

#### E004 — Anonymous Presence Cleared
- `type`: `people.anonymous_off`
- `key`: `people.anonymous`
- `severity`: `info`
- `context`:
  - `source`
  - `confidence`

---

### 2.2 House State

#### E010 — House State Changed
- `type`: `house_state.changed`
- `key`: `house_state.changed`
- `severity`: `info`
- `context`:
  - `from`
  - `to`
  - `reason`

Notes:
- Optional (can be disabled to avoid noise)

---

### 2.3 Occupancy

#### E020 — Room Occupancy Stuck ON (Failsafe)
- `type`: `occupancy.stuck_on`
- `key`: `occupancy.stuck_on.<room_id>`
- `severity`: `warn`
- `context`:
  - `room`
  - `max_on_s`
  - `source_entities` (list)

#### E021 — Occupancy / People Inconsistency (Someone Home, No Room Occupancy)
- `type`: `occupancy.inconsistency_home_no_room`
- `key`: `occupancy.inconsistency_home_no_room`
- `severity`: `info`
- `context`:
  - `anyone_home`
  - `occupied_rooms` (list)

#### E022 — Occupancy / People Inconsistency (Room Occupied, No One Home)
- `type`: `occupancy.inconsistency_room_no_home`
- `key`: `occupancy.inconsistency_room_no_home.<room_id>`
- `severity`: `info`
- `context`:
  - `room`
  - `anyone_home`
  - `source_entities`

---

### 2.4 Lighting

#### E030 — Lighting Manual Hold Enabled
- `type`: `lighting.hold_on`
- `key`: `lighting.hold.<room_id>`
- `severity`: `info`
- `context`:
  - `room`

#### E031 — Lighting Manual Hold Disabled
- `type`: `lighting.hold_off`
- `key`: `lighting.hold.<room_id>`
- `severity`: `info`
- `context`:
  - `room`

#### E032 — Lighting Scene Missing (Misconfiguration)
- `type`: `lighting.scene_missing`
- `key`: `lighting.scene_missing.<room_id>.<intent>`
- `severity`: `warn`
- `context`:
  - `room`
  - `intent`
  - `expected_scene`

---

### 2.5 Heating

#### E040 — Heating Manual Override Detected
- `type`: `heating.manual_override`
- `key`: `heating.manual_override`
- `severity`: `warn`
- `context`:
  - `climate_entity`
  - `observed_change` (mode/temp)
  - `guard_active` (bool)

#### E041 — Heating Command Rate-Limited
- `type`: `heating.rate_limited`
- `key`: `heating.rate_limited`
- `severity`: `info`
- `context`:
  - `climate_entity`
  - `min_seconds_between_commands`

#### E042 — Heating Verify Failed (Retrying)
- `type`: `heating.verify_failed`
- `key`: `heating.verify_failed`
- `severity`: `warn`
- `context`:
  - `climate_entity`
  - `intent`
  - `attempt`
  - `max_retries`

#### E043 — Heating Apply Failed (Final)
- `type`: `heating.apply_failed`
- `key`: `heating.apply_failed`
- `severity`: `crit`
- `context`:
  - `climate_entity`
  - `intent`
  - `error`

---

### 2.6 Security (Read-Only)

#### E050 — Security vs House State Mismatch
- `type`: `security.mismatch`
- `key`: `security.mismatch`
- `severity`: `warn`
- `context`:
  - `security_state`
  - `house_state`
  - `anyone_home`

#### E051 — Armed Away While Someone Home (Inconsistency)
- `type`: `security.armed_away_but_home`
- `key`: `security.armed_away_but_home`
- `severity`: `warn`
- `context`:
  - `security_state`
  - `people_home_list`

---

### 2.7 System / Health

#### E900 — Engine Disabled
- `type`: `system.engine_disabled`
- `key`: `system.engine_disabled`
- `severity`: `info`
- `context`:
  - `reason`

#### E901 — Invalid Configuration (Hard Fail)
- `type`: `system.config_invalid`
- `key`: `system.config_invalid`
- `severity`: `crit`
- `context`:
  - `errors`

#### E902 — Behavior Error (Recovered)
- `type`: `system.behavior_error`
- `key`: `system.behavior_error.<behavior_id>`
- `severity`: `warn`
- `context`:
  - `behavior`
  - `error`

---

## 3. Event Enablement (v1)

Events can be toggled by category:
- `people`
- `occupancy`
- `house_state`
- `lighting`
- `heating`
- `security`
- `system` (always enabled)

Defaults:
- system: enabled
- heating/security: enabled
- people/occupancy: enabled
- house_state: disabled (noise-prone)

---

## 4. Diagnostics & Privacy

- Context must avoid personally sensitive details beyond configured person slugs
- No raw GPS coordinates or device IDs
- Use redaction for entity_id lists if configured

---
