# Heima Notification Recipients Spec v1

## Goal

Decouple notification routing from physical `notify.*` service ids so user/device changes do not require reconfiguring all Heima notification behavior.

## Core Model

### 1. Legacy routes

- `routes` remains supported in v1.
- It is a flat list of `notify.*` service names.
- It is treated as a legacy fallback transport layer.

### 2. Recipient aliases

- `recipients` is a mapping:
  - `recipient_id -> list[notify_service_name]`
- Example:
  - `stefano -> [mobile_app_phone_stefano, mobile_app_mac_stefano]`

Recipient ids are logical identities. They should remain stable even if devices change.

### 3. Recipient groups

- `recipient_groups` is a mapping:
  - `group_id -> list[recipient_id]`
- Example:
  - `family -> [stefano, laura]`

Groups are one-level only in v1:
- group members must be recipient ids
- nested groups are not supported

### 4. Default route targets

- `route_targets` is a list of logical targets used by the event pipeline.
- Each target may be:
  - a `recipient_id`, or
  - a `group_id`

## Routing Resolution

For each emitted event:

1. Start from legacy `routes`
2. Resolve each `route_target`
   - recipient -> its mapped services
   - group -> all recipient services of its members
3. Deduplicate final `notify.*` services
4. Deliver through the existing event pipeline

If a `route_target` does not resolve:
- it is ignored
- a runtime diagnostics/error counter is incremented

## Options Flow Shape (v1)

In `Notifications`:

- `routes` (legacy `notify.*` list)
- `recipients` (textarea; one line per alias: `alias=notify_a,notify_b`)
- `recipient_groups` (textarea; one line per group: `group=recipient_a,recipient_b`)
- `route_targets` (textarea; one alias/group id per line, commas also accepted)

## Compatibility

- `routes` is **not deprecated yet** in v1
- new routing is additive
- the pipeline must work when:
  - only `routes` are configured
  - only aliases/groups are configured
  - both are configured

## Deferred Follow-up

Planned future direction:
- deprecate `routes` once recipient aliases/groups are fully adopted and migration tooling exists
- add per-event or per-category logical routing rules
