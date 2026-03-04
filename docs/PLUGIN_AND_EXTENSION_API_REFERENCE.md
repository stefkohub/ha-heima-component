# Heima Plugin and Extension API Reference

This document explains which APIs are available today for plugin-style integrations and extension code, and which plugin APIs are only planned by spec.

Important distinction:

1. **Normalization plugins**  
   - implemented today
   - used by the runtime
   - combine signals into normalized observations

2. **Policy plugins**  
   - specified, but **not implemented yet**
   - future cross-domain policy extension model

3. **Public extension APIs**  
   - implemented today
   - Home Assistant services/events that external automations or integrations can call/use

---

## 1. Implemented Today: Normalization Plugin API

This is the only true plugin framework currently active in runtime.

Files:
- `custom_components/heima/runtime/normalization/contracts.py`
- `custom_components/heima/runtime/normalization/registry.py`
- `custom_components/heima/runtime/normalization/service.py`
- `custom_components/heima/runtime/normalization/config.py`

### 1.1 Core data contracts

#### `NormalizedObservation`
File:
- `custom_components/heima/runtime/normalization/contracts.py`

Purpose:
- canonical normalized observation from one signal source

Fields:
- `kind: str`
  - signal family, for example:
    - `presence`
    - `boolean_signal`
    - `security`
- `state: str`
  - normalized state string
- `confidence: int`
  - clamped `0..100`
- `raw_state: str | None`
  - original HA raw state if available
- `source_entity_id: str | None`
  - source entity id or logical source key
- `ts: str`
  - UTC ISO timestamp
- `stale: bool`
- `available: bool`
- `reason: str`
  - normalization reason/debug explanation

Method:
- `as_dict() -> dict[str, Any]`

#### `DerivedObservation`
File:
- `custom_components/heima/runtime/normalization/contracts.py`

Purpose:
- normalized observation produced by a fusion strategy/plugin

Extends `NormalizedObservation` and adds:
- `inputs: list[str]`
  - source ids contributing to the derived result
- `fusion_strategy: str`
- `plugin_id: str`
- `plugin_api_version: int`
- `evidence: dict[str, Any]`

This is the standard output type every normalization plugin must return.

---

### 1.2 Plugin interface

#### `FusionPlugin` protocol
File:
- `custom_components/heima/runtime/normalization/registry.py`

A normalization plugin must expose:

- `plugin_id: str`
- `plugin_api_version: int`
- `supported_kinds: tuple[str, ...]`

And must implement:

```python
def derive(
    *,
    kind: str,
    inputs: list[NormalizedObservation],
    strategy_cfg: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> DerivedObservation:
    ...
```

Rules:
- `plugin_id` must be unique
- `supported_kinds` restricts which signal families the plugin can process
- `derive()` must be deterministic for the same inputs/config
- `derive()` must return a valid `DerivedObservation`

Plugins should:
- avoid side effects
- not call HA services directly
- not mutate runtime state directly

They should be treated as pure strategy logic.

---

### 1.3 Registry API

#### `NormalizationFusionRegistry`
File:
- `custom_components/heima/runtime/normalization/registry.py`

Methods:

##### `register(plugin)`
- Registers a plugin instance.
- Raises:
  - `ValueError` if `plugin_id` is empty
  - `ValueError` if `plugin_id` is already registered

##### `get(plugin_id)`
- Returns the registered plugin.
- Raises:
  - `KeyError` if missing

##### `descriptors()`
- Returns a list of `FusionPluginDescriptor`
- Useful for diagnostics/introspection

##### `derive(plugin_id, kind, inputs, strategy_cfg=None, context=None)`
- Resolves the plugin by id
- Validates `supported_kinds`
- Calls the plugin’s `derive()`

This is the low-level execution API.

---

### 1.4 Runtime facade API

#### `InputNormalizer`
File:
- `custom_components/heima/runtime/normalization/service.py`

This is the runtime-facing entry point. Domain code should prefer this facade over using the registry directly.

Methods:

##### `presence(entity_id)`
- Reads a Home Assistant entity and returns a `NormalizedObservation(kind="presence")`

##### `boolean_signal(entity_id)`
- Reads a Home Assistant entity and returns a `NormalizedObservation(kind="boolean_signal")`

##### `boolean_value(value, source_key, reason, confidence=100)`
- Converts a runtime-derived boolean fact into a normalized boolean observation
- Useful for non-entity corroboration paths

##### `security(entity_id, mapping_cfg=None)`
- Reads a bound security source and returns a normalized `security` observation
- Applies raw-to-canonical mapping such as:
  - `armed_away`
  - `armed_home`
  - `disarmed`
  - `transition`
  - `unknown`

##### `derive(kind, inputs, strategy_cfg=None, context=None)`
- High-level fusion entry point
- Uses the registry under the hood
- Applies failure handling and fallback if the plugin fails

This is the main API that runtime/domain code uses.

---

### 1.5 Strategy configuration helpers

File:
- `custom_components/heima/runtime/normalization/config.py`

These helpers exist so domains do not hardcode plugin ids, defaults, and fallback rules in ad hoc ways.

#### `SignalSetStrategyContract`
Reusable contract that defines:
- `allowed_strategies`
- `default_strategy`
- `default_fallback_state`

Built-in contracts:
- `GROUP_PRESENCE_STRATEGY_CONTRACT`
- `ROOM_OCCUPANCY_STRATEGY_CONTRACT`
- `SECURITY_CORROBORATION_STRATEGY_CONTRACT`
- `HOUSE_SIGNAL_STRATEGY_CONTRACT`

#### `normalize_signal_set_strategy_fields(...)`
- Normalizes payload fields coming from config/UI

#### `validate_signal_set_strategy_fields(...)`
- Validates config payloads against the contract

#### `build_signal_set_strategy_cfg(...)`
- Low-level builder for signal-set strategy config

#### `build_signal_set_strategy_cfg_for_contract(...)`
- Preferred high-level builder
- Produces the runtime config dict used by `InputNormalizer.derive()`

This is the stable way domains should assemble fusion config.

---

### 1.6 Built-in plugins currently available

Built-ins are registered from:
- `custom_components/heima/runtime/normalization/builtins.py`

Current built-ins:
- `builtin.direct`
- `builtin.any_of`
- `builtin.all_of`
- `builtin.quorum`
- `builtin.weighted_quorum`

Current usage in runtime:
- room occupancy
- named people quorum
- anonymous presence
- house-state signal composition
- security corroboration

---

### 1.7 Failure handling contract

`InputNormalizer.derive()` already provides runtime-safe failure handling.

If a plugin:
- is missing
- raises an exception
- is used with an unsupported kind

the runtime:
- does **not** crash
- returns a fallback `DerivedObservation`

Supported fallback states:
- `unknown`
- `off`
- `on`

Configured through:
- `strategy_cfg["fallback_state"]`

Diagnostics exposed:
- derive call counts
- plugin error counts
- last plugin error
- last derive metadata
- registered plugins

This means plugin authors do not need to implement their own global runtime safety layer.

---

## 2. Implemented Today: Public Extension APIs

These are not “plugin interfaces” in the same sense as normalization plugins, but they are the supported public integration surfaces that external automations/extensions can use right now.

### 2.1 Home Assistant services

Files:
- `custom_components/heima/services.py`
- `custom_components/heima/services.yaml`

#### `heima.command`

Generic command dispatcher.

Payload:
- `command: str`
- `target: dict` (optional)
- `params: dict` (optional)
- `request_id: str` (optional)

Currently supported commands:
- `recompute_now`
- `set_lighting_intent`
- `set_security_intent`
- `set_room_lighting_hold`
- `notify_event`

#### `heima.set_mode`

Concrete v1 semantics:
- final `house_state` override

Payload:
- `mode`
  - one canonical house state:
    - `away`
    - `home`
    - `guest`
    - `vacation`
    - `sleeping`
    - `relax`
    - `working`
- `state: bool`

Behavior:
- `true`:
  - set the runtime-only final house-state override
- `false`:
  - clear it only if the current override matches that same mode

#### `heima.set_override`

Structured override entry point for specific runtime scopes.

Payload:
- `scope`
- `id`
- `override`

Currently supported scopes:
- `lighting_room_hold`
- `person`

Note:
- `services.yaml` may still show broader historical wording, but current runtime behavior is defined by `custom_components/heima/services.py`

---

### 2.2 Runtime events

Primary event bus event:
- `heima_event`

Event payload is the serialized `HeimaEvent` envelope.

Used by:
- notification pipeline
- debugging
- external automations that want to react to Heima decisions

Other event names exist in constants:
- `heima_snapshot`
- `heima_health`

But the actual actively used public event stream today is:
- `heima_event`

---

## 3. Specified, Not Implemented Yet: Policy Plugin API

This framework is documented but **not implemented** yet.

Spec file:
- `docs/specs/heima_policy_plugin_framework_spec_v1.md`

Purpose:
- future cross-domain policy extension layer
- distinct from normalization plugins

Planned concepts:
- hook points:
  - `pre_policy`
  - `domain_policy`
  - `post_policy`
  - `apply_filter`
- result statuses:
  - `no_change`
  - `augment`
  - `override`
  - `block`
  - `defer`
  - `error`

Important:
- no runtime registry/dispatcher exists yet for policy plugins
- plugins cannot use this API yet in live code

This should be treated as future design, not current implementation surface.

---

## 4. What plugin code should rely on today

If you are writing plugin-style logic **today**, the stable implemented surfaces are:

1. **Normalization plugin contract**
   - `FusionPlugin`
   - `DerivedObservation`
   - `NormalizationFusionRegistry`
   - `InputNormalizer`

2. **Public HA service/event surfaces**
   - `heima.command`
   - `heima.set_mode`
   - `heima.set_override`
   - `heima_event`

Do **not** rely on:
- internal engine private helpers
- uncommitted speculative policy-plugin hooks
- ad hoc direct writes into runtime internals

---

## 5. Recommended plugin authoring rules

For normalization plugins:

- keep `derive()` pure
- do not call HA services
- do not mutate engine/coordinator state
- return explicit `reason` and `evidence`
- support only the `kind`s you actually handle
- use deterministic output for the same inputs/config

For external integrations using public services/events:

- prefer `heima.command`, `heima.set_mode`, `heima.set_override`
- do not depend on internal entity ids as the only integration surface unless the use case is explicitly UI/debug-oriented

---

## 6. Where temporary architectural decisions are documented

Temporary or transitional project decisions are recorded in:

- `docs/PROJECT_DECISIONS.md`

Use that file to understand:
- what is intentionally deferred
- what remains legacy for now
- which compatibility paths are temporary
