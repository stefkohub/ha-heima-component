# Heima — Policy Plugin Framework Mini-SPEC v1

## Purpose

Define a future cross-domain **policy plugin framework** for Heima.

This mini-spec is intentionally forward-looking:
- the current runtime remains primarily fixed-policy
- this spec defines how policy plugins should be introduced cleanly
- it is explicitly separate from the existing **normalization plugin** system

The first intended real adopter is the Heating domain.

---

## 1. Scope

This spec defines:
- what a policy plugin is
- how policy plugins interact with core domain policy
- shared hook points across domains
- conflict resolution and priority rules
- safety, diagnostics, and failure handling

This spec does **not** require immediate implementation in all domains.

---

## 2. Non-Goals

This spec does not:
- replace the normalization layer
- allow plugins to directly call HA services
- allow plugins to own device apply logic
- make every domain fully pluggable from day one

Core apply safety remains in Heima runtime.

---

## 3. Terminology

### 3.1 Normalization Plugin

A normalization plugin:
- combines raw/normalized signals
- returns a `NormalizedObservation` / `DerivedObservation`
- belongs to the Input Normalization Layer

Examples:
- `builtin.any_of`
- `builtin.quorum`
- `builtin.weighted_quorum`

### 3.2 Policy Plugin

A policy plugin:
- operates on domain state and canonical context
- modifies or overrides domain decisions
- never performs device apply directly

Examples:
- heating vacation curve
- lighting time-window policy
- watering weather skip

These are distinct plugin families and must remain separate in implementation and specification.

---

## 4. Core Principle

Each domain keeps a **core policy tree**.

Policy plugins do not replace the domain engine wholesale.  
Instead, they are invoked at explicit hook points and may:
- augment
- override
- block
- defer
- clamp

This preserves:
- deterministic control flow
- centralized safety
- explicit domain ownership

---

## 5. Shared Policy Plugin Contract

Every policy plugin must implement a stable contract.

### 5.1 Required Metadata

- `plugin_id`
- `plugin_api_version`
- `supported_domains`
- `supported_hooks`

### 5.2 Inputs

Each invocation receives:

- `domain`
- `hook`
- canonical runtime context (domain-specific and global)
- current domain decision state
- plugin configuration
- execution metadata (timestamp, entry context, etc.)

The runtime context may include:
- `DecisionSnapshot`
- canonical state (`heima_*`)
- domain-local diagnostics inputs

### 5.3 Output

A plugin returns a structured `PolicyPluginResult`.

Minimum fields:

- `status`
  - `no_change`
  - `augment`
  - `override`
  - `block`
  - `defer`
  - `error`

- `payload`
  - domain-specific structured data

- `reason`
- `priority`
- `diagnostics`
- `plugin_id`
- `plugin_api_version`

### 5.4 Rule

Plugins must return decisions, not side effects.

They must never:
- call `scene.turn_on`
- call `climate.set_temperature`
- mutate runtime state directly

Only the core runtime may apply actions.

---

## 6. Hook Model

The framework defines shared hook points across domains.

### 6.1 `pre_policy`

Purpose:
- enrich or pre-process policy context
- optionally short-circuit simple cases

Typical use:
- derive additional branch metadata
- early suppression of unnecessary work

### 6.2 `domain_policy`

Purpose:
- compute or override the main domain decision

Typical use:
- heating vacation curve
- lighting adaptive scene choice
- watering seasonal scaling

This is the primary hook for domain-specific behavior plugins.

### 6.3 `post_policy`

Purpose:
- adjust a domain decision after core policy has produced a result

Typical use:
- clamp temperatures
- cap watering runtime
- downgrade lighting intensity

### 6.4 `apply_filter`

Purpose:
- final veto/block/defer before apply

Typical use:
- window-open heating block
- rain-based watering block
- security-based lighting suppression

This hook is the natural integration point for future constraints.

---

## 7. Status Semantics

### `no_change`
- plugin observed the context and made no modification

### `augment`
- plugin adds metadata but does not replace the main decision

### `override`
- plugin provides a replacement decision for the current hook

### `block`
- plugin explicitly prevents apply / action progression

### `defer`
- plugin postpones action (e.g. timing or dependency wait)

### `error`
- plugin failed internally; runtime must handle safely

The runtime must define which statuses are terminal for each hook.

---

## 8. Priority and Conflict Rules

Policy plugins must be evaluated deterministically.

### 8.1 Ordering

- plugins have explicit numeric priority
- higher priority runs first
- ties are resolved by stable registration order

### 8.2 Hook Conflict Rules (v1)

Recommended default semantics:

- `pre_policy`
  - multiple `augment` results may accumulate

- `domain_policy`
  - first `override` wins
  - later overrides are ignored and recorded in diagnostics

- `post_policy`
  - multiple `augment`/clamp results may apply in deterministic order

- `apply_filter`
  - first `block` or `defer` is terminal

These semantics must be contractual in the first implementation.

---

## 9. Safety and Failure Handling

Plugin failures must never break the domain runtime.

### 9.1 On Failure

If a plugin throws or returns invalid data:
- the runtime catches the failure
- the plugin result becomes `error`
- the core domain falls back to safe default behavior

### 9.2 Safe Fallback

The safe fallback must be hook-specific:

- `domain_policy`: keep core policy result
- `apply_filter`: do not silently apply an unsafe change; runtime may choose block or no-change depending on domain rules

The fallback strategy must be explicit and diagnosable.

### 9.3 Runtime Integrity

A plugin error must not:
- fail integration setup
- stop evaluation of unrelated domains
- corrupt canonical state

---

## 10. Diagnostics

The framework must expose structured diagnostics per plugin invocation.

Minimum diagnostic data:

- `plugin_id`
- `hook`
- `domain`
- `status`
- `reason`
- whether fallback was used
- conflict/winner information if multiple plugins competed
- plugin execution errors

Diagnostics should exist at two levels:

1. global plugin framework diagnostics
2. domain-local traces showing where a plugin affected behavior

---

## 11. Events

Recommended framework-level events:

- `system.policy_plugin_error`
- `system.policy_plugin_conflict`
- `system.policy_plugin_blocked`

Domain-specific plugins may emit domain events via normal runtime flow, but framework-level failures should be clearly distinguishable.

---

## 12. Domain Examples

### 12.1 Heating

First planned built-in candidate:

- `builtin.heating.vacation_curve`

Hook:
- `domain_policy`

Output example:
- `status = override`
- `payload = { target_temperature, phase, branch="vacation_curve" }`

This maps directly to the current fixed Heating v1 branch and is the first natural candidate for migration once policy pluggability is introduced.

### 12.2 Lighting

Potential future built-in:

- `builtin.lighting.time_windows`

Hook:
- `domain_policy`

Output example:
- override or clamp lighting intent/scene selection

### 12.3 Watering

Potential future built-in:

- `builtin.watering.weather_skip`

Hook:
- `apply_filter`

Output example:
- `status = block`
- `reason = rain_forecast`

---

## 13. Rollout Strategy

Policy pluggability should be introduced incrementally.

### Phase P1 — Framework Only

- define contracts
- add registry and dispatcher
- add diagnostics and error handling
- do not migrate domains yet

### Phase P2 — First Real Domain Adoption

- adopt the framework in Heating first
- move the fixed `vacation_curve` branch behind the framework while preserving behavior

### Phase P3 — Expand to Other Domains

- add targeted policy plugins in Lighting / Watering / Constraints
- keep expansion deliberate and test-backed

This mirrors the successful rollout strategy used for the Normalization Layer.

---

## 14. Relationship with Current Runtime

Current Heima runtime remains valid:
- domains can keep explicit internal policy trees
- policy plugins are a future extension layer

This spec is compatible with the current architecture because:
- it does not require immediate runtime replacement
- it adds a framework for future progressive adoption

---

## 15. First Intended Implementation Target

The first intended real implementation target is:

- **Heating domain**

Reason:
- Heating already has a clear fixed branch that maps naturally to a future plugin (`vacation_curve`)
- the domain is currently being specified from scratch
- migrating Heating first is lower risk than retrofitting Lighting immediately

This makes Heating the correct proving ground for policy pluggability.
