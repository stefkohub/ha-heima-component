# Heima — SPEC v1.1
## Intelligent Home Engine for Home Assistant (Custom Integration)

This document extends **SPEC v1** introducing the **Behavior Framework v1** and clarifying
the extension model for adding new behaviors without modifying the core runtime.

---

## 0. Scope of v1.1

SPEC v1.1 introduces:
- A formal **Behavior Framework** (extension mechanism)
- Explicit **hook points** in the core runtime
- Conflict resolution and priority rules
- Confirmation of **scene-based lighting mapping (per room)**

All other aspects of SPEC v1 remain valid unless explicitly overridden.

---

## 1. Design Goal: Extensible Without Forking the Core

Heima must support:
- Adding new behaviors (e.g. time-based lighting rules)
- Enabling/disabling behaviors per house
- Configuring behaviors via Options Flow
- Maintaining backward compatibility and upgrade safety

Heima **does NOT** support:
- Loading arbitrary user Python code
- Runtime code injection
- YAML-based imperative automations

---

## 2. Behavior Framework v1 — Concept

A **Behavior** is a modular, declarative policy extension that:
- observes canonical state
- optionally alters domain intent decisions
- optionally emits notification events
- never calls HA services directly

Behaviors are:
- shipped with Heima (official behavior packs)
- versioned with Heima
- enabled/configured per installation

---

## 3. Behavior Lifecycle

### 3.1 Registration
At startup, the core runtime registers all built-in behaviors:

- `lighting.time_windows`
- `lighting.base_policy` (always enabled, non-disableable)
- `heating.base_policy`
- `security.consistency`
- `notification.base_policy`

Each behavior declares:
- domain
- priority
- hook types implemented

### 3.2 Enablement
Via Options Flow:
- behaviors can be enabled/disabled
- behavior-specific configuration is stored in the config entry

---

## 4. Hook Points (Behavior API v1)

Behaviors may implement one or more of the following hooks.

### 4.1 `on_snapshot(snapshot) -> BehaviorResult`
- Called after canonical state computation
- Read-only access to full `DecisionSnapshot`
- May emit events (notifications)
- Must NOT modify intents directly

Typical use:
- anomaly detection
- cross-domain checks

---

### 4.2 `lighting_policy(zone_ctx, snapshot) -> IntentDelta | None`
- Called during lighting policy evaluation
- `zone_ctx`: room/zone context (occupancy, holds, mapping)
- `snapshot`: canonical state snapshot

Return value:
- `None` → no change
- `IntentDelta` → proposed intent override or clamp

Example uses:
- time-of-day lighting rules
- night-only brightness clamps
- relax-mode special handling

---

### 4.3 `apply_filter(apply_plan, snapshot) -> ApplyPlan`
- Called before actuation
- Can block, defer, or annotate apply steps
- Cannot introduce new service calls

Typical use:
- safety gating
- dependency ordering
- advanced hold semantics

---

## 5. Conflict Resolution & Priority

Each behavior declares:
- `priority` (integer, higher wins)
- `type`: `hard` or `soft`

Resolution rules:
1. Hard overrides always win over soft suggestions
2. Higher priority wins within same type
3. Base policy provides default intent
4. Final intent must be valid for the domain

Conflicts are logged and surfaced in diagnostics.

---

## 6. BehaviorResult Structure

```text
BehaviorResult:
  - intent_delta (optional)
  - emitted_events (list)
  - debug_notes (optional)
```

---

## 7. Lighting Mapping Model (Confirmed Choice B)

Lighting actuation uses **scene-based mapping per room**.

### 7.1 Mapping Rules
- Each room has one or more scenes per intent
- Zones aggregate rooms
- Per-room manual hold blocks apply **only for that room**
- Zone apply is decomposed into per-room scene activation

### 7.2 Advantages
- Compatible with existing HA scene setups
- Predictable, declarative desired state
- No need to manage individual light entities in Heima

---

## 8. Example: Time Window Lighting Behavior

**Behavior**: `lighting.time_windows`

Config (conceptual):
- scope: room or zone
- condition: time range + house_state
- effect: clamp allowed intents

Example:
- After 23:00 in bedroom:
  - allowed intents: `scene_night` only

Execution:
- Behavior inspects snapshot + time
- Emits `IntentDelta(clamp=[scene_night])`
- Core resolves with base policy

---

## 9. Upgrade & Compatibility Rules

- Behaviors are versioned with Heima
- Adding a behavior = minor version bump
- Changing semantics = major version bump + migration
- Disabled behaviors preserve config but are ignored

---

## 10. Roadmap Impact

### v1.1 (this spec)
- Behavior framework
- Time-window lighting behavior
- Per-room scene mapping enforcement

### v1.2+
- Circadian lighting behavior
- Presence-simulation behavior (vacation)
- User-defined behavior ordering (advanced)

---

## 11. Non-Goals (Explicit)

- User-provided Python scripts
- Arbitrary DSL execution
- Replacing HA automations entirely

Heima remains a **policy engine**, not a scripting platform.

---
