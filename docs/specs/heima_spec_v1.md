# Heima — SPEC v1
## Intelligent Home Engine for Home Assistant (Custom Integration)

---

## 0. Purpose and Principles

**Heima** is a product-grade intelligent home engine distributed as a **Home Assistant custom integration**.
Home Assistant provides the UI, state machine, storage, and device services; Heima provides a **policy-driven control plane**.

### Non‑negotiable principles
1. Heima **creates and owns all canonical entities** (100%).
2. Configuration is done via **Config Entry + Options Flow** (upgrade‑friendly).
3. Policies operate **only on canonical entities**, never directly on raw devices.
4. **Intent‑driven architecture**: policies produce intents; a single orchestrator applies them safely.
5. **Portability**: different houses and sensors — same engine via bindings.

---

## 1. Supported Domains (v1)

Core:
- people
- occupancy
- house_state

Policies:
- lighting
- heating
- security (read‑only)
- notification

---

## 2. Canonical Entity Contract (Created by Heima)

### 2.1 People — Named Persons

For each configured person `<p>` (slug):

- `binary_sensor.heima_person_<p>_home`
- `sensor.heima_person_<p>_confidence` (0–100)
- `sensor.heima_person_<p>_source` (e.g., `ha_person`, `quorum`, `manual`)
- `select.heima_person_<p>_override` (`auto | force_home | force_away`) (optional v1)

Aggregates:
- `binary_sensor.heima_anyone_home`
- `sensor.heima_people_count`
- `sensor.heima_people_home_list` (comma-separated slugs)

### 2.2 People — Unnamed / Anonymous Presence

Heima supports **unnamed (anonymous) presence** for cases such as:
- guests not represented as named persons,
- privacy‑preserving setups,
- generic “someone is home” signals.

Canonical entities:
- `binary_sensor.heima_anonymous_presence`
- `sensor.heima_anonymous_presence_confidence`
- `sensor.heima_anonymous_presence_source`

Aggregation rules:
- `heima_anyone_home = OR(named_people_home, anonymous_presence)`
- `heima_people_count` includes anonymous presence as **+N** (configurable `anonymous_count_weight`, default `1`).

---

## 3. Occupancy Model (Per Room / Zone)

For each room `<r>`:
- `binary_sensor.heima_occ_<r>` (stabilized)
- `sensor.heima_occ_<r>_source`
- `sensor.heima_occ_<r>_last_change` (optional)

For each zone `<z>`:
- `binary_sensor.heima_occ_zone_<z>`

Occupancy is **local presence** and is distinct from people presence.

---

## 4. House State Model

Canonical entities:
- `sensor.heima_house_state`
- `sensor.heima_house_state_reason`

Allowed values:
`away | home | sleeping | working | relax | guest | vacation`

### Priority order (v1)
1. vacation
2. guest
3. away
4. sleeping
5. relax
6. working
7. home

### Determination rules (v1)
1. `vacation_mode` → `vacation`
2. `guest_mode` → `guest`
3. `anyone_home == off` → `away`
4. `sleep_window == on` → `sleeping`
5. `relax == on` → `relax`
6. `work_window == on` → `working`
7. default → `home`

Unnamed presence counts as `anyone_home = on`.

---

## 5. Lighting Domain

Entities:
- `select.heima_lighting_intent_<zone>`
- `binary_sensor.heima_lighting_manual_hold_<room>` (per room)

Intents:
`auto | off | scene_evening | scene_relax | scene_night`

Rules (v1):
- `away/vacation` → `off`
- `sleeping` → `scene_night` when occupied (else `off`)
- `relax` → `scene_relax` when occupied (else `off` or configurable)
- `home/working/guest` → `scene_evening` when occupied (else `off` with idle delay)

Manual hold is **per room** and blocks apply (not intent computation).

Apply mechanisms:
- Preferred: `scene.turn_on`
- Advanced: `script.turn_on` (optional)

---

## 6. Heating Domain

Entities:
- `select.heima_heating_intent`
- `binary_sensor.heima_heating_manual_hold`
- `binary_sensor.heima_heating_applying_guard`

Intents:
`auto | eco | comfort | preheat | off`

Safe apply features (v1):
- rate limiting
- idempotent apply (reconciliation)
- verification & retry
- manual override detection

Apply modes:
- `delegate_to_scheduler` (no temperature writes)
- `set_temperature`
- `hvac_off`

---

## 7. Security Domain (Read‑Only)

Entities:
- `select.heima_security_intent`
- `sensor.heima_security_state`
- `sensor.heima_security_reason`

Purpose:
- consistency checks
- policy constraints
- notification triggers

---

## 8. Notification Domain

Entities:
- `sensor.heima_last_event`
- `sensor.heima_event_stats`

Features:
- event catalog
- deduplication window
- rate limiting per event key
- routing via `notify.*` services

---

## 9. Input Binding (Configurable per House)

### 9.1 People presence methods (per named person)
- `ha_person` (bind to `person.*`)
- `quorum` (multiple sources, threshold-based)
- `manual` (override)

All methods normalize to:
`binary_sensor.heima_person_<p>_home`

### 9.2 Anonymous presence binding
- can bind to occupancy aggregates, mmWave, door sensors, or any generic presence signal
- contributes to `anyone_home` and `house_state`

---

## 10. Safety & Orchestration

- single apply orchestrator
- idempotent reconciliation
- anti‑loop guards
- debounce and dwell times
- restart‑safe behavior

---

## 11. Configuration UX (Options Flow)

1. General
2. People (named + anonymous)
3. Rooms (occupancy)
4. Lighting zones (zone mapping + per-room holds)
5. Heating
6. Security
7. Notifications

---

## 12. Roadmap / Milestones

### Milestone 0 — Spec & Scaffolding
- integration skeleton
- entity contract
- config entry versioning & migrations

### Milestone 1 — Portable MVP
- people (named + anonymous)
- occupancy
- lighting with per-room override
- notifications (basic)

### Milestone 2 — Heating Safe Engine
- rate-limit, guard, verify, retry
- manual override detection
- scheduler delegation

### Milestone 3 — Security & Relax Refinement
- relax as first-class state
- security consistency events

### Milestone 4 — Product Hardening
- migrations
- diagnostics
- tests
- documentation & profiles

---
