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

Output:
- Ciclo completo di evaluation e apply per lighting.
- Eventi standard emessi via bus HA.

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

### 2. Entita Canoniche
- Generazione entita per persone, occupancy, house_state.
- Entita lighting e heating intents.
- Entita security e notification.

### 3. Snapshot e Decision Engine
- Snapshot canonico con stato persone, occupancy e house_state.
- Decision engine per intent lighting e heating.
- Debug notes e diagnostica di snapshot.

### 4. Orchestratore e Apply
- Orchestratore unico per apply.
- Decomposizione zone -> room (scene.turn_on).
- Anti-loop, dedup e idempotenza per scene.
- Hold per room e manual override per heating.

### 5. Notification Pipeline
- Implementare event envelope.
- Dedup e rate limit per key.
- Routing su notify.* configurati.

### 6. Estensioni (Solution A)
- Eventi: heima_event, heima_snapshot (opzionale), heima_health (opzionale).
- Servizi: heima.command, heima.set_mode, heima.set_override.
- Validazione comandi e errori chiari.

### 7. Diagnostica e Privacy
- Diagnostica include mapping, last applied, eventi recenti.
- Redazione di dati sensibili.

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

## Rischi e Mitigazioni
- Config incoerente: validazioni strette + eventi system.config_invalid.
- Loop di apply: anti-loop per room e guard heating.
- Privacy: redazione contesto eventi e snapshot opzionale.

## Prossimi Passi Operativi
1. Implementare scaffolding e modelli dati minimi.
2. Creare Options Flow con validazioni principali.
3. Implementare People e Occupancy adapters.
4. Implementare lighting mapping e orchestratore scene.
5. Implementare event catalog e notification pipeline.
6. Implementare heating safe engine.
7. Integrare Behavior Framework v1.1.
