# Heima — Domain SPEC v1: Watering
## Irrigation / Plant Watering Control Domain

This specification defines the **watering** domain for Heima (control domain).
It supports indoor/outdoor irrigation with safe actuation, schedule compatibility, and sensor-driven decision logic.

---

## 0. Domain Identity

- `domain_id`: `watering`
- `category`: `control`
- Canonical prefix: `heima_watering_*`

---

## 1. Primary Use Cases

1) Scheduled irrigation (external scheduler triggers via Heima intent/command)
2) Sensor-driven watering (soil moisture thresholds, temperature constraints)
3) Vacation/away behavior:
   - optional suppression or reduced watering profile
4) Safety:
   - maximum runtime per cycle
   - lockout window to prevent flooding
   - optional leak/rain inhibit

---

## 2. Scope Model

Watering is scoped by **sector** (also called zone/line):
- `sector_id`: e.g. `balcony`, `garden_front`, `indoor_plants`

A sector typically maps to:
- a `switch.*` (smart plug pump) OR
- a `valve.*` entity OR
- a `script.*` that runs a sequence

Heima treats each sector as an independent actuator with its own safety constraints.

---

## 3. Canonical Entities (Heima-Owned)

For each sector `<s>`:

### 3.1 Intent Select
- `select.heima_watering_intent_<s>`
Options (v1):
- `auto`
- `off`
- `cycle`
- `boost`
- `suspend`

### 3.2 Holds / Safety
- `binary_sensor.heima_watering_hold_<s>` (manual hold; blocks apply)
- `sensor.heima_watering_last_run_<s>` (ISO8601 or timestamp)
- `sensor.heima_watering_last_result_<s>` (`ok|skipped|blocked|failed`)
- `sensor.heima_watering_next_allowed_<s>` (timestamp; lockout until)

### 3.3 Telemetry (optional)
- `sensor.heima_watering_soil_moisture_<s>` (normalized, if bound)
- `sensor.heima_watering_signal_<s>` (string; reason/explainability)

---

## 4. External Inputs (Bindings)

Per sector `<s>` (configured via Options Flow):
- soil moisture sensor (optional)
- rain sensor / forecast inhibit (optional)
- leak sensor inhibit (optional)
- temperature inhibit (optional)
- people/house_state inputs (canonical; always available)
- security state (canonical; read-only)

---

## 5. Intent Semantics

### 5.1 `off`
Never water; apply will ensure actuator is off (or do nothing if safe).

### 5.2 `auto`
Domain base policy determines whether watering is needed now, based on:
- moisture below threshold (if configured)
- schedule trigger window (if external scheduler sets a flag/command)
- not inhibited by rain/leak/temp/security constraints
- not in lockout

If conditions met, `auto` resolves to either `cycle` or `boost` for this evaluation cycle.

### 5.3 `cycle`
Run the configured standard watering cycle duration for the sector.

### 5.4 `boost`
Run an extended duration (config) or run multiple short pulses (config).

### 5.5 `suspend`
Temporary suppression; domain will not water until suspend window expires (config-driven).

---

## 6. Policy (Base) v1

### 6.1 Inputs
- `house_state` (vacation/away/guest/etc.)
- `security_state` (armed_away, etc.)
- sector telemetry (moisture/rain/leak/temp)

### 6.2 Core Rules (default)
- If `heima_house_state in {vacation, away}`:
  - default profile: `auto` but **reduced** (config) OR `suspend` (config)
- If leak sensor ON:
  - force `off`
  - emit event `watering.inhibit.leak`
- If rain inhibit active (rain sensor/forecast):
  - force `suspend` (configurable)
  - emit event `watering.inhibit.rain`
- If moisture >= target:
  - do not water; emit `watering.skip.moisture_ok` (optional noise control)
- If lockout not expired:
  - skip; emit `watering.rate_limited` (info)
- Else if moisture below threshold:
  - choose `cycle` (or `boost` if very low)

Behaviors may override/clamp (e.g. time windows, seasonal profiles).

---

## 7. Mapping Model (Intent → HA Action)

### 7.1 Actuation Targets
Per sector, exactly one of:
- `switch_entity` (on/off)
- `valve_entity` (open/close + optional position)
- `script_entity` (run sequence)

### 7.2 Apply for `cycle` / `boost`
Recommended v1 mapping:
- `script.turn_on` with parameters (duration), OR
- orchestrator executes:
  - turn actuator ON
  - wait duration
  - turn actuator OFF
  - verify OFF

Because long waits inside HA service handlers can be fragile, v1 should prefer a **script** (HA-side) that performs timing,
while Heima provides safety gating and triggers the script.

Therefore mapping options:
- **Mode A (recommended)**: intent→`script.<sector>_cycle` / `script.<sector>_boost`
- Mode B: direct switch/valve timing controlled by Heima (later milestone)

### 7.3 Apply for `off`
- ensure actuator is off OR no-op if already off

### 7.4 Apply for `suspend`
- no actuation; only updates canonical state and lockout window if configured

---

## 8. Safety Controls

Per sector configuration:
- `max_runtime_s` (hard stop; default 600)
- `cycle_runtime_s` (default 120)
- `boost_runtime_s` (default 240)
- `lockout_s` (default 3600)  # minimum time between watering runs
- `min_seconds_between_commands` (default 10)  # actuator protection

Orchestrator requirements:
- record last run timestamp
- enforce lockout and max runtime
- verify actuator OFF at end

---

## 9. Events (Domain Addendum)

Watering domain emits (via Notification pipeline + `heima_event`):

- `watering.run.started` (info) key: `watering.run.<sector>`
- `watering.run.completed` (info) key: `watering.run.<sector>`
- `watering.run.failed` (crit) key: `watering.run.<sector>`
- `watering.inhibit.leak` (warn) key: `watering.inhibit.leak.<sector>`
- `watering.inhibit.rain` (info/warn) key: `watering.inhibit.rain.<sector>`
- `watering.rate_limited` (info) key: `watering.rate_limited.<sector>`

(These should be added to the global Event Catalog in a future minor bump, or referenced as domain-local addendum.)

---

## 10. Options Flow (Domain Section)

Per sector `<s>` fields:
- `sector_id` (slug, immutable)
- `display_name`
- `actuation_mode` (`script|switch|valve`)
- `script_entity` OR `switch_entity` OR `valve_entity`
- optional inhibit sensors:
  - `soil_moisture_entity`
  - `rain_inhibit_entity`
  - `leak_entity`
  - `temp_entity`
- thresholds:
  - `moisture_target` (0..100)
  - `moisture_low` (0..100)
- runtimes:
  - `cycle_runtime_s`, `boost_runtime_s`, `max_runtime_s`
- lockout:
  - `lockout_s`
- vacation profile:
  - `vacation_mode` (`reduce|suspend|normal`)
  - `vacation_reduce_factor` (0..1)

---

## 11. Diagnostics

Include:
- sector config (sanitized)
- last run/result/next allowed
- current inhibits active
- last emitted watering events summary

---
