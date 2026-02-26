# Heima Development Plan

## Status Overview
- Completed: `Phase 0`, `Phase 1`
- Completed: `Phase 2`
- In Progress: `Phase 3` (category toggles + centralized gating implemented; final catalog/heating coverage pending)
- Next: finish Phase 3 residual catalog coverage, then start Normalization Layer rollout (N1-N4) before Heating smartening (`Phase 4`)

## Roadmap (with Normalization Rollout)

1. [x] Phase 0 — Architecture Alignment (Core Setup)
- Define runtime structure for domains/behaviors (`runtime/`, `domains/`, `behaviors/`, `domain_registry`, `orchestrator`).
- Establish core data contracts for `DecisionSnapshot`, `ApplyPlan`, and `HeimaEvent`.
- Implement `HeimaEngine` pipeline foundation: snapshot -> policy -> intents -> apply plan -> apply.

2. [x] Phase 1 — Canonical State + Input Binding
- Implement adapters for People/Anonymous Presence and Occupancy (read from HA state).
- Compute `house_state` and reason according to spec priority.
- Update canonical entities continuously through `CanonicalState`.
- Wire state-change triggers to coordinator refresh (`DataUpdateCoordinator`).

3. [x] Phase 2 — Lighting Domain (Policy + Mapping + Apply)
- Implement base policy (house_state + occupancy) and room-scene mapping.
- Support per-room manual hold and scene fallback.
- Add idempotent/rate-limited apply per room.
- Emit `lighting.*` events from Event Catalog.

4. [ ] Phase 3 — Notification Domain + Event Catalog
- [x] Implement `HeimaEvent` pipeline with dedup/rate-limit.
- [x] Route events to `heima_event` bus and configured `notify.*` services.
- [x] Extend diagnostics with event stats and recent events.
- [x] Wire `heima.command -> notify_event` to unified runtime pipeline (end-to-end).
- [x] Add event category toggles (`people`, `occupancy`, `house_state`, `lighting`, `heating`, `security`; `system` always enabled).
- [x] Centralize runtime event gating before pipeline emission.
- [x] Harden notification routing for startup races (`notify.*` unavailable -> deferred retry, no setup failure).
- [x] Expand core Event Catalog coverage for `people.*`, `occupancy.*`, `lighting.*`, `security.*`, `system.engine_disabled`.
- [ ] Complete remaining Event Catalog coverage (`heating.*`, `security.mismatch`, `system.config_invalid`, `system.behavior_error`) and finalize payload standardization.

5. [ ] Cross-Cut — Input Normalization Layer (Incremental Rollout N1-N5)
- [x] N1 Foundation: add shared normalization contracts + `InputNormalizer` facade + fusion plugin/strategy registry contract (behavior-preserving legacy-backed adapter).
- [ ] N1 Migration: route existing runtime raw reads through the facade (no behavioral change intended).
- [ ] N2 Occupancy: compute room occupancy from normalized presence observations; implement `on_dwell_s` / `off_dwell_s` / `max_on_s`.
- [ ] N2 Diagnostics: expose normalization trace for occupancy sources (raw_state -> normalized_state/reason).
- [ ] N3 Security: normalize alarm raw states to canonical security observation; migrate `security.*` consistency logic to normalized inputs.
- [ ] N4 House Signals + People: normalize house-mode helpers and people source inputs; remove domain-level raw parsing call sites.
- [ ] N5 Advanced Fusion Plugins: support pluggable signal-combination strategies (rule-based, probabilistic, model-based, etc.) behind the same `DerivedObservation` contract.

6. [ ] Phase 4 — Heating Domain (Safe Apply)
- Implement base intents (`auto`, `eco`, `comfort`, `preheat`, `off`).
- Support apply modes (`delegate_to_scheduler`, `set_temperature`).
- Add manual override guard, verification/retry, and rate limiting.
- Emit `heating.*` events.

7. [ ] Phase 5 — Security Domain (Read-Only) + Constraints Layer
- Normalize security state and reason.
- Implement `system.constraints` behavior with precedence order.
- Integrate constraints in `apply_filter` (block/clamp/defer).

8. [ ] Phase 6 — Behavior Framework v1.1
- Implement behavior registry and hook points (`on_snapshot`, `*_policy`, `apply_filter`).
- Add base behaviors and time-window lighting behavior.

9. [ ] Phase 7 — Watering Domain (Spec v1)
- Add Options Flow for sectors and sensor bindings.
- Create canonical watering entities (intent select, hold, telemetry).
- Implement base policy with lockout and max runtime.
- Implement Mode A mapping (script-based apply) and watering events.

## Recent Delivered Work (post Phase 2 hardening)
- Options Flow hardening:
  - fixed edit-step navigation for people/rooms/lighting rooms/zones
  - fixed optional selector clearing (scene/entity/routes) using HA suggested values
  - normalized/finalized options consistently across save paths
- Lighting diagnostics:
  - per-zone trace (`requested_intent`, `final_intent`, `zone_occupied`)
  - per-room trace (scene resolution, skip reason, action)
  - multi-zone room conflict detection in diagnostics
- Lighting runtime:
  - room scene mappings fully optional
  - `off` fallback to `light.turn_off(area_id)` when `scene_off` missing
  - support `room.occupancy_mode = none` (actuation-only rooms)
- Specs updated:
  - room occupancy modes (`derived|none`)
  - zone occupancy ignores non-sensorized rooms
  - lighting `off` fallback semantics
- Automated tests expanded (now includes flow-like options tests, lighting runtime regressions, notify pipeline end-to-end)
- Phase 3 hardening:
  - notification event category toggles in Options Flow
  - centralized event gating in runtime (spec-aligned, `system` always enabled)
  - startup race handling for `notify.*` routes with deferred delivery/retry
  - additional event catalog emissions (`people.*`, `house_state.changed`, occupancy inconsistencies, security inconsistency, zone conflicts)
- Architecture planning:
  - added Input Normalization Layer mini-spec (shared contracts/facade + plugin-based fusion registry + incremental rollout N1-N5) to avoid fragmented smart-policy implementations on raw HA states
