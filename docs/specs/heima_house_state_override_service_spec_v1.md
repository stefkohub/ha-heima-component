# Heima House-State Override Service Spec v1

## Purpose

Define a concrete v1 semantics for `heima.set_mode` so the service name matches
its actual runtime behavior.

In v1, `heima.set_mode` is **not** a boolean signal setter.

It is a **final house-state override service**.

When active, it forces the effective `house_state` to a specific canonical value,
temporarily bypassing normal house-state resolution until the override is
cleared.

---

## Scope

This spec defines:

- the meaning of `heima.set_mode`
- the override data model
- precedence rules
- clearing semantics
- diagnostics / events

This spec does **not** define:

- persistent storage of overrides
- policy-plugin behavior
- boolean mode-signal manipulation

---

## Service Contract

Service:

- `heima.set_mode`

Payload:

- `mode: string`
- `state: boolean`

### Supported `mode` values

`mode` must be one of the canonical house states:

- `away`
- `home`
- `guest`
- `vacation`
- `sleeping`
- `relax`
- `working`

Any unsupported value must raise a validation error.

---

## Runtime Semantics

### `state = true`

Calling:

- `heima.set_mode(mode="<state>", state=true)`

means:

- set the active runtime-only `house_state_override` to `<state>`

Effect:

- the effective `house_state` becomes `<state>`
- normal house-state resolution is bypassed while the override is active

### `state = false`

Calling:

- `heima.set_mode(mode="<state>", state=false)`

means:

- clear the active override **only if** the current `house_state_override`
  equals `<state>`

If the current override is different, the operation is a no-op.

This rule avoids destructive ambiguity when clearing.

---

## Override Model

The runtime maintains a singular per-entry field:

- `house_state_override: Optional[str]`

Properties:

- only one override can be active at a time
- override is runtime-only in v1
- override is cleared on integration reload / unload / restart

There is no stack and no multi-source merge in v1.

Latest successful `state=true` call wins.

---

## Resolution Precedence

Final effective house-state resolution becomes:

1. if `house_state_override` is set:
   - effective `house_state = house_state_override`
   - `house_state_reason = manual_override:<state>`
2. otherwise:
   - run normal house-state resolution from canonical inputs

The override sits above:

- people presence
- anonymous presence
- helper-derived mode signals
- normal policy priority rules

---

## Diagnostics

The runtime must expose the current override state in diagnostics.

Minimum required fields:

- `house_state_override`
- `house_state_override_active` (bool)
- `house_state_override_set_by` (v1 may simply be `service:heima.set_mode`)
- `house_state_override_last_change_ts`

This visibility is required because the override changes global behavior.

---

## Events

The runtime should emit:

- `system.house_state_override_changed`

Context should include:

- `previous`
- `current`
- `source = service:heima.set_mode`
- `action = set | clear | noop`

Notes:

- `set`: override was activated or changed
- `clear`: matching override was removed
- `noop`: `state=false` was called for a non-matching mode

This event belongs to the `system` family because it affects the global runtime
state model.

---

## Validation Rules

The service must reject:

- unsupported `mode`
- missing `mode`
- missing `state`

The service must be accepted even if the requested override equals the current
override. In that case:

- runtime state remains unchanged
- no duplicate state mutation is required
- event emission may be skipped or emitted as a no-op (implementation choice;
  v1 may skip duplicate-no-op events)

---

## Interaction With Domains

Domains must consume the effective final `house_state`, not the raw derived
pre-override state, unless they are explicitly inspecting diagnostics.

This means the override affects all domains consistently:

- Lighting
- Heating
- Security
- future Watering

This is the main reason the service semantics must operate at the final
house-state layer.

---

## Relationship With Future Policy Plugins

This service remains valid even after policy plugins exist.

Future policy plugins should receive:

- the already effective `house_state`

If a future plugin needs awareness of the override, it should inspect runtime
context/diagnostics rather than redefine override semantics.

This keeps `heima.set_mode` orthogonal to the future policy-plugin framework.

---

## Non-Goals (v1)

The following are intentionally excluded:

- persistent overrides across restarts
- per-user or per-source ownership
- override expiration / TTL
- multiple simultaneous overrides
- separate boolean mode-signal management via `heima.set_mode`

If boolean signal control is ever needed, it must use a different service with a
different name.

---

## Implementation Notes

Recommended v1 implementation approach:

1. store a runtime-only `house_state_override` on the engine/coordinator state
2. make `resolve_house_state(...)` consume the override first
3. update canonical `house_state` / `house_state_reason`
4. emit `system.house_state_override_changed`
5. expose diagnostics

This keeps the behavior explicit and aligned with the service name.
