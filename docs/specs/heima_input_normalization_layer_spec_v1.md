# Heima — Input Normalization Layer SPEC v1 (Incremental Introduction)

## 1. Purpose

This spec defines a **single, shared input normalization layer** for Heima.

Goal:
- normalize raw Home Assistant states into stable Heima observations
- enable smarter policies (dwell, mismatch handling, confidence, stale detection)
- avoid fragmented ad-hoc parsers in runtime domains

This spec is intentionally designed for **incremental rollout** without a later large refactor.

---

## 2. Problem Statement

Current Heima runtime mixes:
- raw HA reads (`person`, `binary_sensor`, `sensor`, `alarm_control_panel`, helpers)
- canonical Heima state (`heima_*`)

Risk if extended without a normalization layer:
- duplicated parsing logic
- inconsistent semantics (`on/off`, `unknown`, `unavailable`)
- smart policies implemented on top of fragile raw parsing
- expensive refactor later

---

## 3. Architecture Principle (Mandatory)

### 3.1 Single Entry Point
All new smart policy logic MUST consume normalized observations through a single runtime facade.

Runtime domains MUST NOT introduce new raw parsing helpers directly in domain logic.

### 3.2 Incremental Adoption
The layer may be introduced gradually, but the **contracts and facade are defined first**.

Temporary legacy adapters are allowed only:
- behind the normalization facade
- with behavior-preserving semantics

### 3.3 Backward Compatibility (Limited / Necessary)
Backward compatibility is only required for:
- existing config entries
- behavior-preserving initial migration to the facade

No long-term dual parsing paths should remain in domain code.

---

## 4. Core Contracts

## 4.1 `NormalizedObservation` (base)
Fields (conceptual contract):
- `kind` (`presence`, `security`, `boolean_signal`, ...)
- `state` (normalized canonical state string)
- `confidence` (`0..100`)
- `raw_state` (original HA state string or `None`)
- `source_entity_id`
- `ts` (observation timestamp)
- `stale` (bool)
- `available` (bool)
- `reason` (normalization reason / mapping path)

Notes:
- `state` is domain-specific but canonical within that observation kind
- `reason` is diagnostic and should be stable enough for troubleshooting

## 4.2 Specialized Observation Types (v1)

### Presence Observation
- `kind = "presence"`
- canonical states:
  - `on`
  - `off`
  - `unknown`

### Security Observation
- `kind = "security"`
- canonical states (minimum v1 set):
  - `armed_away`
  - `armed_home`
  - `disarmed`
  - `unknown`
  - `unavailable`
  - `transition` (optional in v1 rollout, required before advanced security policies)

### Boolean Signal Observation (house-mode helpers etc.)
- `kind = "boolean_signal"`
- canonical states:
  - `on`
  - `off`
  - `unknown`

---

## 5. Runtime Facade (Mandatory)

Heima runtime uses a single facade (name indicative):
- `InputNormalizer`

Required methods in v1:
- `presence(entity_id) -> PresenceObservation`
- `boolean_signal(entity_id) -> NormalizedObservation`
- `security(entity_id, mapping_cfg) -> SecurityObservation`

Optional later:
- `person(entity_id)`
- `numeric_signal(entity_id, thresholds_cfg)`

Rule:
- runtime engine/domain policy code should depend on this facade, not on raw HA state parsing

---

## 6. Semantics Rules (v1)

## 6.1 Unknown / Unavailable
- `unknown` and `unavailable` are distinct at normalization level
- policy layers may collapse them, but normalization must preserve the distinction where possible

## 6.2 Confidence
- v1 may use simple confidence values (e.g. `0/100`) if no richer model exists yet
- confidence calculation logic must be centralized in normalizer implementations

## 6.3 Staleness
- v1 rollout may set `stale = false` by default if freshness metadata is not yet available
- field is still contractual to avoid future interface churn

## 6.4 Diagnostic Traceability
Normalization should preserve enough info (`raw_state`, `reason`) to debug why a policy fired or did not fire.

---

## 7. Incremental Rollout Plan (Anti-Fragmentation)

## 7.1 Phase N1 — Foundation (Behavior Preserving)
- Introduce contracts + `InputNormalizer` facade
- Implement facade using current raw parsing semantics (legacy-backed adapter)
- Update runtime call sites to use the facade (no intended behavior changes)

Outcome:
- architecture migration without policy changes
- future smart features can build on the facade safely

## 7.2 Phase N2 — Occupancy First (High Value)
- room occupancy computation consumes `PresenceObservation`
- implement room dwell semantics (`on_dwell_s`, `off_dwell_s`, `max_on_s`) on normalized observations
- add diagnostics for room source normalization

## 7.3 Phase N3 — Security Normalization
- normalize alarm raw states into canonical `SecurityObservation`
- move `security.*` mismatch and consistency logic to normalized security states
- add transition/arming state handling (if exposed by source integration)

## 7.4 Phase N4 — House Signals + People
- normalize house mode helper signals via `boolean_signal()`
- migrate people methods (`ha_person`, `quorum`, `manual`) to normalized inputs
- optional confidence/staleness improvements

---

## 8. Migration Constraints (Professional Guardrails)

To prevent fragmentation during rollout:

1. No new domain logic may call raw parsing helpers directly if a normalizer method exists.
2. Raw helpers may remain temporarily only as implementation details of the normalizer.
3. New smart policies MUST consume normalized observations.
4. Diagnostics should expose normalization output for migrated flows.

---

## 9. Non-Goals (v1)

- full probabilistic sensor fusion
- per-device ML confidence models
- universal schema for every HA domain

The goal is a clean, extensible foundation with incremental migration safety.

