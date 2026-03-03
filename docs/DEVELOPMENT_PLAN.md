# Heima - Piano di Sviluppo (v1.x)

## Sintesi
Heima e una integrazione custom per Home Assistant che fornisce un motore di policy per casa intelligente. L'integrazione crea e possiede entita canoniche, calcola stato della casa e intenti, e applica azioni in modo sicuro tramite un orchestratore. L'architettura e intent-driven e supporta un framework di behavior estendibile senza fork del core.

## Obiettivi v1
- Integrare i domini core: people, occupancy, house_state, lighting, heating, security (read-only), notifications.
- Implementare Options Flow completo con validazione forte e salvataggio in config entry.
- Creare tutte le entita canoniche con unique_id stabili.
- Eseguire policy e applicazioni tramite orchestratore con sicurezza, dedup, rate limit.
- Esporre eventi e servizi pubblici secondo la strategia di estensione (Solution A).

## Presupposti
- Home Assistant fornisce UI, storage, servizi, eventi e registry entita.
- Heima non controlla device direttamente, ma solo tramite scene e climate service.
- Le specifiche v1 e v1.1 sono contratti stabili per v1.x.

## Milestone
1. Milestone 0 - Scaffolding e Contratto Entita
2. Milestone 1 - MVP Portabile
3. Milestone 2 - Heating Safe Engine
4. Milestone 1.1 - Behavior Framework v1

## Stato Milestone (aggiornato)
- Milestone 0: raggiunta.
- Milestone 1: sostanzialmente raggiunta per il perimetro MVP portabile (people, anonymous, occupancy, house_state, lighting, notification pipeline base, diagnostica principale).
- Milestone 2: sostanzialmente raggiunta come Heating MVP sicuro (branch per `house_state`, safe apply, vacation curve, scheduler condiviso, osservabilita, test). Restano rifiniture e validazione manuale finale.
- Milestone 1.1: non ancora implementata; il Behavior Framework resta pianificato.
- Cross-cut Normalization Layer: rollout avanzato e gia integrato nei path runtime principali; il framework e ormai oltre lo stato sperimentale.
- Cross-cut Policy Plugin Framework: definito a livello spec, non ancora implementato nel runtime.

## Milestone 0 - Scaffolding e Contratto Entita
- Inizializzare struttura integrazione.
- Creare manifest, const, setup/unload entry, logger e diagnostics stub.
- Definire registry delle entita canoniche.
- Definire modelli dati per config entry e runtime state.

Output:
- Integrazione caricabile.
- Entita canoniche registrate senza logica di policy.

## Milestone 1 - MVP Portabile
- People adapter.
- Anonymous presence.
- Occupancy per room e zone.
- House state deterministico.
- Lighting intent per zone e apply per room con hold.
- Notification pipeline base con event catalog e rate limit.
- Hardening UX/config flow per modifiche opzioni e diagnostica lighting.

Output:
- Ciclo completo di evaluation e apply per lighting.
- Eventi standard emessi via bus HA.
- Tracciabilita diagnostica di decisioni lighting (zone/room trace).

## Milestone 2 - Heating Safe Engine
- Intenti heating e selettore canonico.
- Orchestratore heating con rate limit, guard, verify e retry.
- Rilevamento override manuale e notifiche.

Output:
- Heating applicato in modo sicuro e idempotente.

## Milestone 1.1 - Behavior Framework v1
- Registro behaviors built-in.
- Hook points: on_snapshot, lighting_policy, apply_filter.
- Risoluzione conflitti hard/soft con priorita.
- Behavior lighting.time_windows.

Output:
- Behavior configurabili via Options Flow.
- Policy lighting estendibile.

## Stream di Lavoro
### 1. Config Flow e Options Flow
- Implementare flusso opzioni completo secondo spec.
- Validazioni su entity_id, slug univoci, quorum.
- Migrazioni config entry v1.x.
- Stato attuale:
  - implementato e funzionante
  - corretti bug di persistenza/edit form
  - corretta gestione campi selector opzionali clearabili
  - aggiunto supporto room `occupancy_mode = none`

### 2. Entita Canoniche
- Generazione entita per persone, occupancy, house_state.
- Entita lighting e heating intents.
- Entita security e notification.

### 3. Snapshot e Decision Engine
- Snapshot canonico con stato persone, occupancy e house_state.
- Decision engine per intent lighting e heating.
- Debug notes e diagnostica di snapshot.
- Stato attuale:
  - people/anonymous/occupancy/house_state implementati
  - lighting intent/apply v1 implementato
  - room senza sensori (`occupancy_mode = none`) supportate
  - zone occupancy calcolata ignorando room non sensorizzate
  - input normalization layer plugin-first introdotto e usato nei path principali (occupancy, people quorum, house signals, security)
  - occupancy plugin-first con dwell/max_on operativo e `weighted_quorum` disponibile
  - plugin layer esteso oltre la presenza:
    - corroborazione security (`boolean_signal`)
    - composizione house helper (`boolean_signal`)
  - contratti riusabili di strategia (`SignalSetStrategyContract`) introdotti per:
    - group presence
    - room occupancy
    - security corroboration
    - house signals
  - Heating MVP implementato:
    - branch built-in per `house_state`
    - `fixed_target`
    - `vacation_curve`
    - `heima.set_mode` come final house-state override

### 4. Orchestratore e Apply
- Orchestratore unico per apply.
- Decomposizione zone -> room (scene.turn_on).
- Anti-loop, dedup e idempotenza per scene.
- Hold per room e manual override per heating.
- Stato attuale:
  - apply lighting integrato nel runtime engine (scene + fallback `light.turn_off(area)`)
  - idempotenza/rate-limit per room implementati
  - diagnostica conflitti room-in-piu-zone presente
  - apply Heating integrato nel runtime engine (`climate.set_temperature`) con guard e rate-limit
  - orchestratore separato non ancora estratto

### 5. Notification Pipeline
- Implementare event envelope.
- Dedup e rate limit per key.
- Routing su notify.* configurati.
- Stato attuale:
  - implementato (bus `heima_event` + routing `notify.*`)
  - `heima.command notify_event` integrato nel pipeline unificato
  - sensori `heima_last_event` / `heima_event_stats` aggiornati
  - manca copertura completa Event Catalog v1

### 6. Estensioni (Solution A)
- Eventi: heima_event, heima_snapshot (opzionale), heima_health (opzionale).
- Servizi: heima.command, heima.set_mode, heima.set_override.
- Validazione comandi e errori chiari.
- Stato attuale:
  - `heima.command` operativo per i comandi implementati
  - `heima.set_mode` operativo come override finale runtime-only del `house_state`
  - `heima.set_override` operativo per gli override gia supportati

### 7. Diagnostica e Privacy
- Diagnostica include mapping, last applied, eventi recenti.
- Redazione di dati sensibili.
- Stato attuale:
  - diagnostics globali del normalizer (`registered_plugins`, error counters, last fallback/error)
  - trace locali nei punti runtime rilevanti:
    - `presence.group_trace`
    - `occupancy.room_trace`
    - `security.observation_trace`
    - `security.corroboration_trace`
    - `house_signals.trace`
    - `house_state_override`
    - `runtime.scheduler`

### 8. Localizzazione
- Traduzioni base en/it per labels e errori.

### 9. Heating Domain (implementation track)
- H4.1 Domain Foundation
  - bind `climate_entity`
  - creare entita canoniche heating reali
  - aggiungere diagnostica base heating
  - introdurre il config model base:
    - `apply_mode`
    - `temperature_step`
    - `manual_override_guard`
    - `override_branches`
- H4.2 Safe Apply Path
  - leggere setpoint corrente
  - guard `small_delta`
  - rate limit / idempotenza
  - manual override guard
  - prime emissioni `heating.apply_skipped_small_delta` / `heating.manual_override_blocked`
- H4.3 Vacation Timing Bindings
  - collegare sensori/helper per:
    - `hours_from_start`
    - `hours_to_end`
    - `total_hours`
    - `is_long`
    - `outdoor_temperature`
  - modellare i relativi binding espliciti nel config heating
- H4.4 Vacation Curve Policy Branch
  - introdurre il branch selector per `house_state`
  - supportare catalogo built-in:
    - `scheduler_delegate`
    - `fixed_target`
    - `vacation_curve`
  - implementare `vacation_curve` con `eco_only`, `ramp_down`, `cruise`, `ramp_up`
  - safety floor da temperatura esterna
  - quantizzazione sul passo termostato
- H4.5 Normal Branch Semantics
  - rendere esplicito il comportamento scheduler-following fuori da `vacation`
- H4.6 Events and Observability
  - introdurre eventi heating iniziali e trace diagnostico strutturato
- H4.7 Automated Tests
  - unit test policy curve
  - runtime test branch/apply guard
  - HA e2e test con `ConfigEntry`
- Stato attuale:
  - implementato come Heating MVP
  - `scheduler_delegate`, `fixed_target`, `vacation_curve`
  - safe apply con:
    - manual hold
    - thermostat preset manual-override detection
    - small-delta skip
    - rate limit / idempotenza
  - scheduler condiviso usato per timed rechecks del `vacation_curve`
  - eventi:
    - `heating.vacation_phase_changed`
    - `heating.target_changed`
    - `heating.branch_changed`
    - `heating.apply_skipped_small_delta`
    - `heating.manual_override_blocked`
    - `heating.apply_rate_limited`
    - `heating.vacation_bindings_unavailable`

### 10. Policy Plugin Framework (future track)
- P0 Spec Foundation
  - mini-spec cross-domain definita
  - separazione esplicita da normalization plugins
  - Heating identificato come primo adopter futuro
- P1 Framework Only
  - introdurre registry policy plugin
  - dispatcher per hook `pre_policy`, `domain_policy`, `post_policy`, `apply_filter`
  - diagnostica e gestione errori/fallback
- P2 First Real Adoption
  - migrare Heating `vacation_curve` da branch fisso a primo built-in policy plugin, senza cambiare comportamento
- P3 Domain Expansion
  - estendere con cautela a Lighting / Watering / Constraints dopo stabilizzazione Heating

## Modello Dati (Sintesi)
- People: binary_sensor, sensor confidence, source, override.
- Anonymous presence: binary_sensor, confidence, source.
- Occupancy: binary_sensor per room e zone.
- House state: sensor state + reason.
- Lighting: select intent per zone, hold per room.
- Heating: select intent, hold, applying_guard.
- Security: select intent, state, reason.
- Notification: last_event, event_stats.

## Testing e Qualita
- Test unit per decision logic e mapping fallback.
- Test integration per Options Flow e servizi.
- Test di idempotenza apply.
- Validazione rate limit e dedup eventi.
- Stato attuale:
  - test unit + runtime + servizi + flow-style tests presenti
  - aggiunto harness HA reale (`pytest-homeassistant-custom-component`)
  - presenti test end-to-end con `ConfigEntry` e setup integrazione per:
    - room occupancy dwell
    - `weighted_quorum`
    - people quorum
    - anonymous presence
    - fail-safe fallback path
    - security corroboration trace
    - house helper signal trace
  - coperti regression bug principali (selector clear, lighting conflicts, notify pipeline)
  - aggiunti test HA e2e per Heating e scheduler runtime
  - suite locale: `120 passed`

## Rischi e Mitigazioni
- Config incoerente: validazioni strette + eventi system.config_invalid.
- Loop di apply: anti-loop per room e guard heating.
- Privacy: redazione contesto eventi e snapshot opzionale.

## Prossimi Passi Operativi
1. Completare `Phase 3`: espandere Event Catalog e standardizzare payload/eventi per domini.
2. Fare una validazione manuale finale del Heating MVP in HA reale (branch editing, timed progression, `set_mode`).
3. Rafforzare lighting con policy conflitti zone-room configurabile (`first_wins/priority`).
4. Avviare `Phase 5` (Security + Constraints) quando Heating v1 e considerato stabile.
5. Mantenere il Policy Plugin Framework come track separato: nessuna implementazione runtime finche Heating v1 fisso non e stabile.
6. Dopo stabilizzazione Heating, valutare `P1` (framework-only) come prossimo step architetturale.
7. Integrare Behavior Framework v1.1.
