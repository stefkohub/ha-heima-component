# Heima — Specifications Index
## Versioned Product Specifications

This folder contains the canonical, versioned specifications for the Heima Home Assistant integration.

---

## Specs (current)

### Core Product Specs
- **SPEC v1** — Product scope, domains, canonical entities, milestones  
  File: `heima_spec_v1.md`

- **SPEC v1.1** — Behavior Framework v1 (extensible policies) + scene-based lighting mapping  
  File: `heima_spec_v1_1_behavior_framework.md`

### UX / Configuration
- **Options Flow SPEC v1** — UI steps, fields, validation, defaults, runtime effects  
  File: `heima_options_flow_spec_v1.md`

### Runtime Interfaces
- **Event Catalog SPEC v1** — Standard events, keys, severity, payloads, dedup/rate-limit rules  
  File: `heima_event_catalog_spec_v1.md`

- **Mapping Model SPEC v1** — Lighting mapping (room→scenes, zone→rooms), holds, behavior clamps, fallback, idempotency  
  File: `heima_mapping_model_spec_v1.md`

- **Extension Strategy SPEC v1 (Solution A)** — Event bus + HA services interface for third-party extensions  
  File: `heima_extension_strategy_spec_v1_solution_a.md`

---

## Versioning Rules (Summary)

- Backward compatible additions → **minor** bump (e.g., v1.1, v1.2)
- Breaking changes (semantics, schema, required fields) → **major** bump (e.g., v2.0)
- Event envelope fields and service command schemas are **contractual** in v1.x

---

## Suggested Repository Layout

```
custom_components/heima/
  __init__.py
  manifest.json
  config_flow.py
  const.py
  coordinator.py
  runtime/
    engine.py
    snapshot.py
    state_store.py
    orchestrator.py
    behaviors/
      __init__.py
      base_lighting.py
      lighting_time_windows.py
  entities/
    sensors.py
    binary_sensors.py
    selects.py
  services.py
  diagnostics.py
  translations/
    en.json
    it.json
docs/
  specs/
    heima_spec_v1.md
    heima_spec_v1_1_behavior_framework.md
    heima_options_flow_spec_v1.md
    heima_event_catalog_spec_v1.md
    heima_mapping_model_spec_v1.md
    heima_extension_strategy_spec_v1_solution_a.md
    INDEX.md
```

---

## Next Implementation Milestones

1. **Integration skeleton**
   - manifest, constants, setup/unload entry
   - basic logging + diagnostics stub

2. **Config Flow + Options Flow**
   - implement Options Flow exactly as per spec
   - store normalized options in config entry

3. **Canonical Entity Registry**
   - create all canonical entities from config
   - ensure stable unique_ids and friendly names

4. **Runtime Engine MVP**
   - People (ha_person + quorum + anonymous)
   - House state calculation
   - Lighting zone intent + per-room scene apply with holds

5. **Notification pipeline**
   - event catalog emission
   - dedup/rate-limit + routing

6. **Heating safe orchestrator**
   - rate limit, guard, verify/retry, manual override detect

---
