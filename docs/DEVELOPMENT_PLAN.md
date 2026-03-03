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
  - heating ancora stub (solo entita/config)

### 4. Orchestratore e Apply
- Orchestratore unico per apply.
- Decomposizione zone -> room (scene.turn_on).
- Anti-loop, dedup e idempotenza per scene.
- Hold per room e manual override per heating.
- Stato attuale:
  - apply lighting integrato nel runtime engine (scene + fallback `light.turn_off(area)`)
  - idempotenza/rate-limit per room implementati
  - diagnostica conflitti room-in-piu-zone presente
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

### 7. Diagnostica e Privacy
- Diagnostica include mapping, last applied, eventi recenti.
- Redazione di dati sensibili.
- Stato attuale:
  - diagnostics globali del normalizer (`registered_plugins`, error counters, last fallback/error)
  - trace locali nei punti runtime rilevanti:
    - `presence.group_trace`
    - `occupancy.room_trace`
    - `security.observation_trace`

### 8. Localizzazione
- Traduzioni base en/it per labels e errori.

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
  - coperti regression bug principali (selector clear, lighting conflicts, notify pipeline)
  - suite locale: `82 passed`

## Rischi e Mitigazioni
- Config incoerente: validazioni strette + eventi system.config_invalid.
- Loop di apply: anti-loop per room e guard heating.
- Privacy: redazione contesto eventi e snapshot opzionale.

## Prossimi Passi Operativi
1. Completare `Phase 3`: espandere Event Catalog e standardizzare payload/eventi per domini.
2. Implementare `Phase 4` Heating safe engine (apply modes, guard, verify/retry, rate limit).
3. Rafforzare lighting con policy conflitti zone-room configurabile (`first_wins/priority`).
4. Valutare chiusura o ulteriore espansione di `N5` (provider esterni / plugin piu generici) dopo il merge del branch `normalisation`.
5. Integrare Behavior Framework v1.1.
