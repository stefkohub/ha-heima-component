# Heima Development Plan

## Status Overview
- Completed: `Phase 0`, `Phase 1`
- Next: `Phase 2`

## 8-Phase Roadmap

1. [x] Phase 0 — Architecture Alignment (Core Setup)
- Define runtime structure for domains/behaviors (`runtime/`, `domains/`, `behaviors/`, `domain_registry`, `orchestrator`).
- Establish core data contracts for `DecisionSnapshot`, `ApplyPlan`, and `HeimaEvent`.
- Implement `HeimaEngine` pipeline foundation: snapshot -> policy -> intents -> apply plan -> apply.

2. [x] Phase 1 — Canonical State + Input Binding
- Implement adapters for People/Anonymous Presence and Occupancy (read from HA state).
- Compute `house_state` and reason according to spec priority.
- Update canonical entities continuously through `CanonicalState`.
- Wire state-change triggers to coordinator refresh (`DataUpdateCoordinator`).

3. [ ] Phase 2 — Lighting Domain (Policy + Mapping + Apply)
- Implement base policy (house_state + occupancy) and room-scene mapping.
- Support per-room manual hold and scene fallback.
- Add idempotent/rate-limited apply per room.
- Emit `lighting.*` events from Event Catalog.

4. [ ] Phase 3 — Notification Domain + Event Catalog
- Implement `HeimaEvent` pipeline with dedup/rate-limit.
- Route events to `heima_event` bus and configured `notify.*` services.
- Extend diagnostics with event stats and recent events.

5. [ ] Phase 4 — Heating Domain (Safe Apply)
- Implement base intents (`auto`, `eco`, `comfort`, `preheat`, `off`).
- Support apply modes (`delegate_to_scheduler`, `set_temperature`).
- Add manual override guard, verification/retry, and rate limiting.
- Emit `heating.*` events.

6. [ ] Phase 5 — Security Domain (Read-Only) + Constraints Layer
- Normalize security state and reason.
- Implement `system.constraints` behavior with precedence order.
- Integrate constraints in `apply_filter` (block/clamp/defer).

7. [ ] Phase 6 — Behavior Framework v1.1
- Implement behavior registry and hook points (`on_snapshot`, `*_policy`, `apply_filter`).
- Add base behaviors and time-window lighting behavior.

8. [ ] Phase 7 — Watering Domain (Spec v1)
- Add Options Flow for sectors and sensor bindings.
- Create canonical watering entities (intent select, hold, telemetry).
- Implement base policy with lockout and max runtime.
- Implement Mode A mapping (script-based apply) and watering events.
