# Heima — Mapping Model SPEC v1
## Room→Scenes, Zone→Rooms, Intent→Scene, Holds, Behavior Clamps, Fallback

This document defines the **mapping model** used by Heima to translate:
- canonical **lighting intents** (per zone) into **scene activations** (per room),
- with support for **per-room manual holds**, **behavior clamps**, and **fallback rules**.

Heima uses **Choice B**: *room-based scenes* (no direct light entity control).

---

## 0. Core Concepts

### 0.1 Room
A **Room** is a logical unit configured in Heima:
- has occupancy signals
- has optional manual lighting hold
- has a mapping from intents to scenes

### 0.2 Zone (Lighting Zone)
A **Zone** is an aggregation of one or more rooms.
- Lighting policy computes an intent per zone
- Actuation applies the intent **per room** within the zone

### 0.3 Intent
A **Lighting Intent** is a stable enum. v1 defines:
- `auto`
- `off`
- `scene_evening`
- `scene_relax`
- `scene_night`

### 0.4 Scene
A Home Assistant scene entity (domain `scene`) that encodes desired state for a set of devices.

---

## 1. Data Model (Config Entry / Options Storage)

### 1.1 Rooms
Each room has:
- `room_id` (slug, immutable)
- `display_name`
- `manual_hold_enabled` (bool)
- `scene_map` (dict intent→scene_entity_id)

`scene_map` keys must include at least one of:
- `scene_evening`
- `scene_relax`
- `scene_night`
- `off`

### 1.2 Zones
Each lighting zone has:
- `zone_id` (slug)
- `display_name`
- `rooms` (list of room_id)
- `intent_select_entity` (created by Heima: `select.heima_lighting_intent_<zone>`)

---

## 2. Normalization Rules

### 2.1 Zone Intent Normalization
If zone intent is `auto`, Heima resolves it to a concrete intent based on house_state:
- sleeping → `scene_night`
- relax → `scene_relax`
- home/working/guest → `scene_evening`
- away/vacation → `off`

(Exact rules owned by lighting policy; mapping consumes final concrete intent.)

### 2.2 Behavior Clamps and Overrides
Behaviors may return `IntentDelta` with:
- `override`: force a specific intent
- `clamp`: restrict allowed intents

Resolution produces a **final zone intent**.

---

## 3. Apply Decomposition (Zone → Rooms)

Given:
- Zone Z with rooms R1..Rn
- Final zone intent `I`

Heima constructs a per-room plan:
- for each room R:
  - if `manual_hold(R) == on` → skip room apply
  - else determine scene S = `scene_map(R)[I]` (fallback allowed)
  - then call `scene.turn_on` for S

This provides **true per-room override** and partial apply.

---

## 4. Fallback Rules (Room Scene Selection)

For each room R and desired intent I:

1. If `scene_map(R)` contains I → use it
2. Else if I is `scene_relax`:
   - fallback to `scene_evening` if present
3. Else if I is `scene_evening`:
   - fallback to `scene_relax` if present
4. Else if I is `scene_night`:
   - fallback to `scene_evening` if present, else `off`
5. Else if I is `off`:
   - use `off` scene if present, otherwise no-op

If no usable scene exists:
- emit event `lighting.scene_missing` (warn)
- skip apply for that room

---

## 5. Manual Hold Semantics (Per Room)

Canonical entity:
- `binary_sensor.heima_lighting_manual_hold_<room_id>`

Rules:
- When hold is ON:
  - Heima does not apply any lighting scene to that room
  - Heima still computes and publishes zone intents (observability)
- When hold flips OFF:
  - Next evaluation may apply current desired intent to the room (subject to throttling)

Recommended UX:
- Hold toggles should be presented in room dashboards

---

## 6. Idempotency & Deduplication

### 6.1 Per-Room Last Applied Tracking
Heima stores (in memory + optional diagnostics):
- `last_applied_scene[room_id]`
- `last_applied_ts[room_id]`

Apply rules:
- If desired scene equals last_applied_scene within a short window → skip apply
- If user manually changes lights (hold OFF) → the next apply may reconcile to desired state

### 6.2 Anti-Loop Window
Heima may apply a loop suppression window per room to avoid reacting immediately to its own apply.

---

## 7. Zone Membership Conflicts

A room **may** appear in multiple zones, but v1 recommends:
- each room belongs to **exactly one lighting zone**

If a room is configured in multiple zones:
- highest priority zone wins (priority configured or deterministic ordering)
- Heima emits event `lighting.zone_conflict` (warn) (v1.1 optional)

---

## 8. Example Configuration (Conceptual)

Room mappings:
- bedroom:
  - scene_evening: `scene.bedroom_evening`
  - scene_relax: `scene.bedroom_relax`
  - scene_night: `scene.bedroom_night`
  - off: `scene.bedroom_off`

Zone:
- `bedroom_zone` includes [bedroom]
- intent select: `select.heima_lighting_intent_bedroom_zone`

If zone intent becomes `scene_night`:
- apply `scene.bedroom_night` unless hold is ON

---

## 9. Diagnostics Requirements

Mapping model must be included in diagnostics:
- rooms, zones, scene maps
- last applied per room
- scene missing events history
- hold state summary

---
