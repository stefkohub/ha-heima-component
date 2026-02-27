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

Incremental delivery MUST remain plugin-first:
- N2/N3/N4 domain migrations must use the fusion registry and plugin contracts introduced in N1
- no interim domain-specific fusion implementations are allowed outside the normalization layer

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

## 4.3 `DerivedObservation` (Fusion Output Contract)
`DerivedObservation` is the canonical output of a signal fusion method (built-in or plugin).

It MUST be shape-compatible with `NormalizedObservation`, and SHOULD add:
- `inputs` (list of source observation refs / ids)
- `fusion_strategy` (e.g. `any_of`, `quorum`, `weighted_quorum`, `custom.<id>`)
- `plugin_id` (stable plugin identifier)
- `plugin_api_version`
- `evidence` (diagnostic summary; compact and non-sensitive)

Rule:
- runtime domains consume the derived output exactly like any other normalized observation
- domains MUST NOT depend on algorithm-specific internals

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
- `derive(kind, inputs, strategy_cfg) -> DerivedObservation`

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

## 6.5 Fusion Output Stability
All fusion strategies (including future third-party or model-based plugins) MUST return canonical observation fields:
- `state`
- `confidence`
- `reason`
- `available`
- `stale`

This prevents runtime/domain refactors when new fusion methods are introduced.

---

## 7. Fusion Plugins (Extensible, v1 Contract / v1.x Implementations)

## 7.1 Goal
Heima MUST support pluggable signal fusion methods so future strategies of any kind can be added without changing runtime domain code.

## 7.2 Fusion Plugin Interface (Conceptual Contract)
A fusion plugin receives:
- normalized input observations (not raw HA states)
- a strategy configuration payload
- optional execution context (time, room/zone id, domain usage)

And returns:
- a `DerivedObservation`

Minimum plugin metadata:
- `plugin_id` (stable, unique)
- `plugin_api_version` (e.g. `1`)
- `supported_kinds` (e.g. `presence`)

## 7.3 Plugin Registry (Mandatory)
Fusion methods are resolved through a central registry (name indicative: `NormalizationFusionRegistry`).

Built-in strategies should be registered through the same registry as external plugins.

Examples of strategy ids:
- `direct`
- `any_of`
- `all_of`
- `quorum`
- `weighted_quorum` (future)
- `custom.<name>` (future)
- `external.<provider>.<name>` (future)

Rule:
- domains/runtime MUST NOT instantiate or select fusion implementations directly
- domains ask the normalization layer / registry to execute a strategy by id

## 7.4 Failure Handling (Mandatory)
If a fusion plugin fails:
- the runtime MUST NOT crash
- a fallback behavior must be applied (configured or default)
- diagnostics must capture plugin failure context

Default fallback behavior (v1):
- emit a normalized/derived observation with `state = unknown`
- `confidence = 0`
- `available = false`
- `reason = plugin_error`

Recommended event (v1.x):
- `normalization.plugin_error` (diagnostic severity; category can be `system` or future `normalization`)

## 7.5 Security / Privacy Guardrails for Plugins
Plugins MUST operate on normalized observations by default.

If a future plugin requires raw/history access:
- that access must be explicit in plugin config/capabilities
- diagnostics must redact sensitive fields consistently with integration diagnostics policy

---

## 8. Incremental Rollout Plan (Anti-Fragmentation)

## 8.1 Phase N1 — Foundation (Behavior Preserving)
- Introduce contracts + `InputNormalizer` facade
- Introduce fusion plugin interface + central registry contract (behavior-preserving built-ins only)
- Implement facade using current raw parsing semantics (legacy-backed adapter)
- Update runtime call sites to use the facade (no intended behavior changes)

Outcome:
- architecture migration without policy changes
- future smart features can build on the facade safely

## 8.2 Phase N2 — Occupancy First (Plugin-First)
- room occupancy computation consumes `PresenceObservation`
- route current `any_of` / `all_of` logic through built-in fusion plugins (`builtin.any_of`, `builtin.all_of`)
- implement room dwell semantics (`on_dwell_s`, `off_dwell_s`, `max_on_s`) on normalized observations
- add diagnostics for room source normalization
- occupancy policies MUST call the normalization facade/registry (no direct fusion logic in occupancy domain code)

### 8.2.1 Operational Rules (N2)
For each room with `occupancy_mode = derived`:
1. Normalize all room `sources` with `InputNormalizer.presence(...)`.
2. Execute fusion via registry plugin selected by room logic:
   - `logic = any_of` -> `plugin_id = builtin.any_of`
   - `logic = all_of` -> `plugin_id = builtin.all_of`
3. Use resulting `DerivedObservation` as room occupancy candidate input.
4. Apply dwell state machine before publishing effective room occupancy:
   - `on_dwell_s`: candidate `on` must persist before effective `on`
   - `off_dwell_s`: candidate `off` must persist before effective `off`
   - `max_on_s`: force effective `off` if continuously on beyond threshold

### 8.2.2 Runtime State (N2)
Per derived room, runtime keeps:
- `candidate_state` and `candidate_since`
- `effective_state` and `effective_since`
- `forced_off_by_max_on` (bool/timestamp)

### 8.2.3 Unknown Handling (N2)
`candidate_state = unknown` MUST NOT produce immediate effective `on`.

Recommended baseline behavior:
- keep current effective state while unknown is transient
- allow transition to effective `off` only through explicit `off` candidate dwell (or `max_on_s` enforcement)

### 8.2.4 Diagnostics (N2)
Diagnostics for each derived room MUST include:
- source-level normalized observations (`raw_state`, `state`, `confidence`, `available`, `reason`)
- fused `DerivedObservation` (`plugin_id`, `fusion_strategy`, `state`, `confidence`, `reason`, `evidence`)
- dwell state machine internals (`candidate_*`, `effective_*`, dwell thresholds, max-on enforcement)

## 8.3 Phase N3 — Security Normalization (Plugin-First)
- normalize alarm raw states into canonical `SecurityObservation`
- move `security.*` mismatch and consistency logic to normalized security states
- add transition/arming state handling (if exposed by source integration)
- any multi-signal security corroboration MUST be implemented via normalization strategies/plugins

## 8.4 Phase N4 — House Signals + People (Plugin-First)
- normalize house mode helper signals via `boolean_signal()`
- migrate people methods (`ha_person`, `quorum`, `manual`) to normalized inputs
- optional confidence/staleness improvements
- quorum/combination behavior for people inputs MUST be executed via the plugin registry path

## 8.5 Phase N5 — Plugin Ecosystem Expansion (Optional v1.x+)
- add additional built-in strategies (e.g. `weighted_quorum`, temporal compositors)
- support external strategy packages/providers with explicit registration/capability checks
- expose strategy selection/configuration in Options Flow where justified
- add richer plugin diagnostics, health, and failure fallback policies

---

## 9. Migration Constraints (Professional Guardrails)

To prevent fragmentation during rollout:

1. No new domain logic may call raw parsing helpers directly if a normalizer method exists.
2. Raw helpers may remain temporarily only as implementation details of the normalizer.
3. New smart policies MUST consume normalized observations.
4. Diagnostics should expose normalization output for migrated flows.
5. New fusion logic MUST be implemented as a normalization strategy/plugin, not inside domain policy code.
6. Plugin outputs MUST conform to `DerivedObservation` contract (no domain-specific custom payloads).
7. N2/N3/N4 deliveries are invalid if they bypass the plugin registry for signal combination.

---

## 10. Non-Goals (v1)

- mandatory built-in advanced fusion strategies in v1 baseline
- mandatory external/model-based plugins in v1 baseline
- per-device model-specific confidence systems in core runtime baseline
- universal schema for every HA domain

The goal is a clean, extensible foundation with incremental migration safety and plugin-ready fusion contracts for any signal-combination approach.
