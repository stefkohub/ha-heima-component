# Heima — Domain Framework SPEC v1
## How to Add New Domains Without Rewriting the Core

This specification defines the **Domain Framework v1** for Heima.
A “domain” is a bounded module that can:
- create canonical entities (Heima-owned),
- compute intents (policy),
- map intents to HA actions (actuation),
- emit standard events (notification pipeline),
- integrate with the Behavior Framework.

The Domain Framework is designed for **product-grade maintainability**: adding domains should be localized, versioned, and testable.

---

## 0. Definitions

- **Canonical Entity**: an HA entity created/owned by Heima (`heima_*`), stable contract.
- **Intent**: desired high-level outcome, not a direct service call.
- **Apply**: controlled actuation via Heima orchestrator (safety, idempotency, verify).
- **Domain**: a module that owns a set of intents, entities, mapping, and events.

---

## 1. Domain Categories

### 1.1 Control Domains (Actuation)
Domains that actuate devices/services and require safety/verify:
- lighting, heating, notification, watering, device_control, etc.

### 1.2 Read-Only Domains
Domains that normalize external state and constrain behavior:
- security (read-only), weather signals, etc.

### 1.3 Analytics Domains (No Actuation)
Domains that compute/report, no device control:
- energy analytics, plant health analytics, etc.

---

## 2. Domain Contract (Interfaces)

A domain is implemented as a Python package with the following components.

### 2.1 `DomainDefinition`
Metadata + enablement.

Fields:
- `domain_id` (slug): e.g. `lighting`, `watering`
- `category`: `control|readonly|analytics`
- `version`: domain schema version (int)
- `depends_on` (list of canonical signals): e.g. `house_state`, `security_state`
- `entities_factory`: callable that returns canonical entities to create
- `policy`: callable(s) that produce intents (or behavior hooks)
- `mapping`: intent→action mapping model (control domains)
- `events`: catalog keys used by this domain

### 2.2 `DomainRuntime`
Runtime object instantiated per config entry.

Responsibilities:
- validate its own config
- provide evaluation functions (policy)
- contribute apply steps to orchestrator
- emit domain events via notification pipeline
- provide diagnostics fragment

Methods (conceptual):
- `validate(config) -> list[errors]`
- `create_entities(registry) -> None`
- `evaluate(snapshot) -> DomainDecision`
- `build_apply_plan(snapshot, decision) -> DomainApplyPlan`
- `diagnostics() -> dict`

---

## 3. Canonical Entities Rules

### 3.1 Naming
All entities are prefixed:
- `heima_<domain>_*`

Room/zone scoped entities:
- `heima_<domain>_<kind>_<scope_id>`
Examples:
- `select.heima_watering_intent_balcony`
- `binary_sensor.heima_lighting_manual_hold_bedroom`

### 3.2 Ownership
- Heima creates entities, sets state, and defines unique_id.
- External integrations may read but must not assume internal implementation.

---

## 4. Domain Configuration Model

Domains are configured via Options Flow under their own section.

Rules:
- no free-text entity IDs (use entity selectors)
- all schema changes are versioned and migrated
- domain config is stored under:
  - `options["domains"][<domain_id>] = {...}` (recommended)
  - or top-level `options[<domain_id>]` for v1 simplicity

---

## 5. Evaluation Pipeline Integration

The global evaluation pipeline remains:

1) compute canonical People/Occupancy/HouseState (+ read-only security normalization)
2) run domain policies (base policy + behaviors)
3) write canonical intent entities
4) build apply plan
5) apply with safety and idempotency

Domains integrate at steps (2) and (4).

---

## 6. Orchestrator Integration (Control Domains)

### 6.1 Apply Plan Contribution
Each control domain contributes apply steps:
- target (room/zone/device)
- action type (scene/script/service)
- parameters
- verify strategy (optional)
- rate limit (optional)
- gating constraints (holds, engine enabled, security constraints)

### 6.2 Safety & Idempotency
Orchestrator enforces:
- per-target serialization
- dedup (skip same desired)
- loop suppression window
- verify/retry if configured

Domains may *request* verify but orchestrator *executes* it.

---

## 7. Event Integration

Domains emit events as:
- `HeimaEvent` objects into the notification pipeline
- optionally also as HA `heima_event` event bus events (via notification domain)

All event types must be registered in the Event Catalog spec (or domain-local addendum).

---

## 8. Behavior Framework Integration

Behaviors are the preferred mechanism to extend domain logic without adding new domains.

Rules:
- a domain may expose additional behavior hook points (domain-specific), but v1 uses:
  - `on_snapshot(snapshot)`
  - `<domain>_policy(ctx, snapshot)`
  - `apply_filter(plan, snapshot)`

A domain provides:
- a **base policy behavior** (non-disableable) that produces defaults,
- optional behaviors enabled via Options Flow.

---

## 9. Repository Layout (Recommended)

```
custom_components/heima/
  runtime/
    engine.py
    orchestrator.py
    snapshot.py
    domain_registry.py
    domains/
      lighting/
        __init__.py
        domain.py
        policy_base.py
        behaviors/
      heating/
      watering/
        domain.py
        policy_base.py
        mapping.py
        behaviors/
      energy/
```

---

## 10. Adding a New Domain (Checklist)

1) Create package `runtime/domains/<domain_id>/`
2) Implement `domain.py` exporting `DOMAIN_DEFINITION`
3) Implement:
   - canonical entities factory
   - base policy
   - mapping (control domains) or analytics computation
4) Add Options Flow section
5) Add diagnostics fragment
6) Add tests:
   - config validation
   - policy evaluation
   - apply plan generation
7) Register domain in `DomainRegistry`

---

## 11. Versioning Rules

- adding a new domain is a **minor** bump (v1.x)
- changing entity names, intent enums, or mapping semantics is **major**
- domain-specific schema changes require migration with defaults

---
