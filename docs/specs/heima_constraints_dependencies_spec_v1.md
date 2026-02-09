# Heima — Constraints & Dependencies SPEC v1
## Cross-Domain Constraints, Precedence, and Gating (Product-Grade)

This specification defines how Heima handles **dependencies between concepts** (house modes, security state, overrides)
and how these dependencies influence **domain decisions** and **actuation** in a consistent, maintainable way.

The goal is to avoid “if vacation then …” logic scattered across domains, by introducing:
- canonical dependency signals (in the snapshot),
- a unified constraint layer,
- deterministic precedence rules,
- configurable policies (per domain) for global modes (vacation/guest/relax/etc.).

---

## 0. Key Principles

1. **Canonical signals only**: dependencies evaluate on Heima canonical entities or snapshot fields.
2. **Decide vs Apply separation**:
   - Domains compute intents (decision).
   - The orchestrator applies intents via an apply plan (apply).
3. **Single constraint layer**: cross-domain constraints are enforced centrally (apply_filter).
4. **Configurable but bounded**: users can configure constraints via Options Flow without injecting arbitrary logic.
5. **Explainability**: every constraint decision must be attributable (reason strings + events).

---

## 1. Canonical Dependency Signals (Snapshot Fields)

Heima snapshot MUST include these canonical dependency signals:

### 1.1 Global Modes / State
- `house_state` (`away|home|sleeping|working|relax|guest|vacation`)
- `anyone_home` (bool)
- `people_count` (int)
- `occupied_rooms` (list of room_id)

### 1.2 Security (Read-Only)
- `security_state` (string normalized; e.g. `disarmed|armed_home|armed_away|triggered|unknown`)
- `security_reason` (string)

### 1.3 Overrides / Holds (Canonical)
- `engine_enabled` (bool)
- `lighting_room_hold[room_id]` (bool)
- `heating_hold` (bool)
- `watering_sector_hold[sector_id]` (bool)
- `person_override[person_slug]` (enum optional)

### 1.4 Time Signals (External Scheduler Compatible)
Heima does not implement its own scheduler in v1, but it MUST be able to consume time signals:
- `sleep_window` (bool)
- `work_window` (bool)
- `vacation_active` (bool) — typically mapped into `house_state`
- `vacation_end_ts` (optional)
- `vacation_hours_to_end` (optional computed helper, canonical sensor)

---

## 2. Constraint Layer Architecture

Constraints are implemented as a non-disableable behavior:
- `system.constraints` implementing `apply_filter(apply_plan, snapshot) -> ApplyPlan`

Responsibilities:
- apply global gating rules
- enforce security-dependent blocks
- enforce domain enablement and per-scope holds
- annotate apply steps with reasons
- emit events for blocked/modified actions

**Domains MUST NOT directly bypass constraints**.

---

## 3. Constraint Evaluation Order (Precedence)

Constraints are evaluated in this strict order:

1. **Engine Disabled**
2. **Hard Safety Inhibits** (e.g., leak inhibit for watering; heating guard; explicit emergency states)
3. **Security Constraints** (armed_away blocks selected domains)
4. **House State Constraints** (vacation/away/sleeping/guest/relax)
5. **Scope Holds** (per-room/per-sector/per-domain holds)
6. **Rate Limit / Idempotency** (orchestrator-level)
7. **Soft Constraints** (noise reduction, optional clamps)

If a step is blocked at a higher precedence level, lower levels do not override it.

---

## 4. Constraint Types

### 4.1 Hard Block
- Removes apply steps (no actuation).
- Emits event `system.constraint_blocked` (warn) with reason.

### 4.2 Modify / Clamp
- Rewrites apply steps to safer alternatives (e.g. dim/night scene instead of evening).
- Emits event `system.constraint_modified` (info/warn).

### 4.3 Defer
- Keeps desired intent but postpones apply until conditions clear.
- Emits event `system.constraint_deferred` (info).

---

## 5. Domain Participation Model

Each domain declares its “mode policy” in config:
- how it behaves under certain house/security states

The constraint layer uses these declarations to apply consistent gating.

### 5.1 Mode Policy Structure (Conceptual)
Per domain:
- `enabled` (bool)
- `blocked_when_security` (list of security states)
- `blocked_when_house_state` (list of house states)
- `mode_overrides` (dict house_state -> override intent or clamp)
- `allow_when_armed_away` (bool) or more granular

This structure is configured via Options Flow.

---

## 6. Standard Constraint Rules (v1 Defaults)

### 6.1 Engine Disabled
If `engine_enabled == false`:
- no apply steps are executed
- canonical state may still be computed (optional setting)
- emit event `system.engine_disabled` (info)

### 6.2 Security Armed Away
If `security_state == armed_away`:
- default block domains:
  - lighting (optional, configurable)
  - device_control (default block)
  - watering (default allow or block configurable)
- heating is NOT blocked by default, but may be clamped to eco

Emit:
- `security.mismatch` if inconsistent with people/house state
- `system.constraint_blocked` when an apply step is removed due to armed_away

### 6.3 House State Vacation
If `house_state == vacation`:
- lighting: force `off` except selected zones (config)
- heating: apply vacation policy (see 7)
- watering: apply vacation profile (reduce/suspend/normal)
- notifications: remain enabled

### 6.4 Sleeping
If `house_state == sleeping`:
- lighting: clamp to `scene_night/off`
- device_control: optional block for noisy devices
- notifications: rate-limit more aggressively (optional)

---

## 7. Heating: Vacation + Preheat Dependency Model

This section codifies the example dependency requested.

### 7.1 Heating Vacation Policy Options
Config enum:
- `delegate_to_scheduler` (scheduler manages; Heima does not set temps)
- `eco_hold` (Heima sets an eco target continuously)
- `hvac_off` (turn heating off)
- `eco_then_preheat` (eco until preheat window, then preheat)

### 7.2 Preheat Window Inputs (External Scheduler Compatible)
Heima uses one of:
- `vacation_end_ts` (datetime) and computes `hours_to_end`
- `vacation_hours_to_end` (already computed canonical sensor)

Config:
- `preheat_hours_before_end` (float, default 4.0)
- `preheat_intent` (default `preheat`)
- `eco_intent` (default `eco`)

Rule:
- If `house_state == vacation` AND `hours_to_end <= preheat_hours_before_end`:
  - heating intent becomes `preheat`
  - else heating intent is `eco` (or chosen vacation base intent)

This rule should be implemented as a **heating behavior**:
- `heating.vacation_preheat` (enabled when vacation policy requires it)

---

## 8. Watering: Security/Vacation Dependencies

Default:
- watering is allowed during `armed_away` (common for outdoor systems), but can be blocked.
- during `vacation`, watering uses per-sector `vacation_mode`:
  - `reduce|suspend|normal`

Constraints layer:
- blocks watering if leak inhibit is active (hard)
- blocks watering if user has set sector hold (scope hold)

---

## 9. Constraint Events (v1 Addendum)

The constraints layer emits standardized events via the notification pipeline:

- `system.constraint_blocked` (warn)
  - key: `system.constraint_blocked.<domain>.<scope>`
  - context: {domain, scope, reason, security_state, house_state}

- `system.constraint_modified` (info/warn)
  - key: `system.constraint_modified.<domain>.<scope>`
  - context: {domain, scope, from, to, reason}

- `system.constraint_deferred` (info)
  - key: `system.constraint_deferred.<domain>.<scope>`
  - context: {domain, scope, reason}

These should be added to the global Event Catalog in a minor update, or referenced as a system addendum.

---

## 10. Diagnostics Requirements

Diagnostics MUST include:
- current constraint configuration
- most recent constraint decisions per domain/scope
- last N constraint events
- a summary of blocked/modified/deferred counts

---
