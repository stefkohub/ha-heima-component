# Heima — Extension Strategy SPEC v1 (Solution A)
## Event-Based Extensions (No Public Python API Object)

This document defines the **extension strategy** for Heima using **Solution A**:
- integrations/extensions interact with Heima **only via HA events and HA services**
- **no** public Python API object is exposed in `hass.data`
- the interface is stable, testable, and safe for third-party distribution

---

## 0. Goals

- Allow third-party integrations (HACS) to:
  - observe Heima decisions (snapshots/intents)
  - observe Heima events (notification catalog, anomalies)
  - request controlled actions (commands) without bypassing safety
- Keep Heima core maintainable:
  - stable contract
  - minimal coupling
  - strict ownership of canonical entities and actuation

---

## 1. Public Interface Surface

Heima exposes exactly:
1. **Events** (HA Event Bus):
   - `heima_event`
   - `heima_snapshot` (optional, can be disabled for privacy/noise)
   - `heima_health` (optional)
2. **Services** (HA service registry):
   - `heima.command`
   - `heima.set_mode`
   - `heima.set_override`
   - `heima.emit_test_event` (debug only, optional)
3. **Canonical Entities**
   - consumers may read Heima entities, but must not assume internal implementation details

No other extension points are supported in v1.

---

## 2. Events

### 2.1 `heima_event`
Purpose: publish standardized events from the Event Catalog v1 through HA event bus.

Event name:
- `heima_event`

Payload fields (envelope):
- `event_id` (string)
- `ts` (ISO8601)
- `key` (string)
- `type` (string)
- `severity` (`info|warn|crit`)
- `title` (string)
- `message` (string)
- `context` (dict; redacted)

Notes:
- This is the primary extension signal. Consumers subscribe and react.

---

### 2.2 `heima_snapshot` (optional)
Purpose: expose a summarized decision snapshot for observability and third-party behaviors external to Heima.

Event name:
- `heima_snapshot`

Payload (minimal, privacy-conscious):
- `snapshot_id` (string)
- `ts` (ISO8601)
- `house_state` (string)
- `anyone_home` (bool)
- `people_count` (int)
- `occupied_rooms` (list of room_ids)
- `lighting_intents` (dict zone_id -> intent)
- `heating_intent` (string)
- `security_state` (string)
- `notes` (string optional)

Controls:
- Can be enabled/disabled in Options Flow (recommended default: disabled)
- When disabled, only `heima_event` is emitted

---

### 2.3 `heima_health` (optional)
Purpose: publish health transitions.

Event name:
- `heima_health`

Payload:
- `ts`
- `health_ok` (bool)
- `reason` (string)

---

## 3. Services

All services enforce:
- engine enabled/disabled state
- authorization expectations (HA standard)
- internal safety rules (orchestrator remains the only actuator)

### 3.1 `heima.command`
Purpose: allow external integrations to request supported operations.

Service:
- `heima.command`

Required fields:
- `command` (string)
- `target` (dict, optional)
- `params` (dict, optional)
- `request_id` (string, optional)

Supported commands (v1):
- `recompute_now`
- `set_lighting_intent`
- `set_heating_intent`
- `set_security_intent`
- `set_room_lighting_hold`
- `notify_event` (inject custom event through notification pipeline)

Command specifics:

#### `recompute_now`
- params: none
- effect: enqueue evaluation cycle (coalesced)

#### `set_lighting_intent`
- target:
  - `zone_id` (string)
- params:
  - `intent` (enum, must be valid for zone)
  - `mode` (`temporary|sticky`) default: `temporary`
- effect:
  - writes to `select.heima_lighting_intent_<zone>` following core rules
  - triggers apply (subject to holds/behaviors)

#### `set_heating_intent`
- params:
  - `intent` (`auto|eco|comfort|preheat|off`)
  - `mode` (`temporary|sticky`) default `temporary`

#### `set_security_intent`
- params:
  - `intent` (`auto|armed_away|armed_home|disarmed`)
  - note: security is read-only for actuation; intent affects consistency expectations

#### `set_room_lighting_hold`
- target:
  - `room_id`
- params:
  - `hold` (bool)

#### `notify_event`
- params:
  - `type` (string)
  - `key` (string)
  - `severity` (`info|warn|crit`)
  - `title` (string)
  - `message` (string)
  - `context` (dict)
- effect:
  - event is processed by Heima notification pipeline (dedup/rate-limit/routes)
  - then emitted as `heima_event`

Validation:
- unknown commands rejected with clear error
- invalid intent values rejected

---

### 3.2 `heima.set_mode`
Purpose: convenience wrapper for high-level modes.

Service:
- `heima.set_mode`

Fields:
- `mode` (enum: `engine_enabled`, `guest`, `vacation`, `relax`)
- `state` (bool)

Effect:
- toggles the corresponding Heima canonical entity / internal state
- triggers recompute

---

### 3.3 `heima.set_override`
Purpose: convenience wrapper for per-person or per-room overrides.

Service:
- `heima.set_override`

Fields:
- `scope` (`person|room|heating`)
- `id` (person_slug or room_id)
- `override` (string/bool depending on scope)

Examples:
- person override: `auto|force_home|force_away`
- room hold: bool
- heating hold: bool

---

## 4. Backward Compatibility Rules

- Event envelope fields are stable in v1.x
- Adding new event types is a **minor** change
- Removing/renaming fields or changing semantics is a **major** change
- Services:
  - new commands may be added (minor)
  - changing command schema is major

---

## 5. Security & Privacy

- `context` must remain redacted:
  - no precise GPS
  - no raw device identifiers unless explicitly allowed
- `heima_snapshot` is optional and recommended off by default

---

## 6. Extension Examples

### 6.1 Third-party “Quiet Hours” integration
- subscribes to `heima_snapshot`
- if time is within quiet hours, calls `heima.command` `set_lighting_intent` (temporary) to clamp to night scene

### 6.2 External anomaly notifier
- subscribes to `heima_event`
- forwards crit events to PagerDuty or email

---

## 7. Non-Goals

- Loading third-party Python modules into Heima runtime
- Runtime behavior injection
- Bypassing orchestrator safety

---
