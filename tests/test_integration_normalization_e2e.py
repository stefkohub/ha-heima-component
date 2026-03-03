from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heima.const import DOMAIN
from custom_components.heima.runtime.normalization import InputNormalizer, NormalizationFusionRegistry


def _entry(options: dict) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Heima",
        data={},
        options=options,
    )


def _room_entity_state(hass: HomeAssistant, room_id: str):
    return hass.states.get(f"binary_sensor.heima_occupancy_{room_id}") or hass.states.get(
        f"binary_sensor.heima_occ_{room_id}"
    )


def _anon_source_state(hass: HomeAssistant):
    return hass.states.get("sensor.heima_anonymous_presence_source") or hass.states.get(
        "sensor.heima_anonymous_source"
    )


class _ExplodingAnyOfPlugin:
    plugin_id = "builtin.any_of"
    plugin_api_version = 1
    supported_kinds = ("presence",)

    def derive(self, *, kind, inputs, strategy_cfg=None, context=None):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_e2e_room_occupancy_dwell_transitions_after_timer(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    entry = _entry(
        {
            "rooms": [
                {
                    "room_id": "studio",
                    "occupancy_mode": "derived",
                    "sources": ["binary_sensor.room_presence"],
                    "logic": "any_of",
                    "on_dwell_s": 1,
                    "off_dwell_s": 0,
                }
            ]
        }
    )
    entry.add_to_hass(hass)

    hass.states.async_set("binary_sensor.room_presence", "off")
    await hass.async_block_till_done()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    occupancy = _room_entity_state(hass, "studio")
    assert occupancy is not None
    assert occupancy.state == "off"

    hass.states.async_set("binary_sensor.room_presence", "on")
    await hass.async_block_till_done()

    assert _room_entity_state(hass, "studio").state == "off"

    await asyncio.sleep(1.2)
    await hass.async_block_till_done()

    assert _room_entity_state(hass, "studio").state == "on"


@pytest.mark.asyncio
async def test_e2e_room_weighted_quorum_uses_threshold_and_source_weights(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    entry = _entry(
        {
            "rooms": [
                {
                    "room_id": "studio",
                    "occupancy_mode": "derived",
                    "sources": [
                        "binary_sensor.presence_a",
                        "binary_sensor.presence_b",
                        "binary_sensor.presence_c",
                    ],
                    "logic": "weighted_quorum",
                    "weight_threshold": 1.2,
                    "source_weights": {
                        "binary_sensor.presence_a": 0.4,
                        "binary_sensor.presence_b": 0.8,
                        "binary_sensor.presence_c": 0.2,
                    },
                    "on_dwell_s": 0,
                    "off_dwell_s": 0,
                }
            ]
        }
    )
    entry.add_to_hass(hass)

    hass.states.async_set("binary_sensor.presence_a", "off")
    hass.states.async_set("binary_sensor.presence_b", "off")
    hass.states.async_set("binary_sensor.presence_c", "off")
    await hass.async_block_till_done()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    occupancy = _room_entity_state(hass, "studio")
    assert occupancy is not None
    assert occupancy.state == "off"

    hass.states.async_set("binary_sensor.presence_a", "on")
    await hass.async_block_till_done()
    assert _room_entity_state(hass, "studio").state == "off"

    hass.states.async_set("binary_sensor.presence_b", "on")
    await hass.async_block_till_done()
    assert _room_entity_state(hass, "studio").state == "on"

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    trace = coordinator.engine.diagnostics()["occupancy"]["room_trace"]["studio"]
    assert trace["plugin_id"] == "builtin.weighted_quorum"
    assert trace["configured_source_weights"]["binary_sensor.presence_b"] == 0.8
    assert trace["effective_source_weights"]["binary_sensor.presence_b"] == 0.8


@pytest.mark.asyncio
async def test_e2e_person_quorum_updates_home_sensor_and_group_trace(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    entry = _entry(
        {
            "people_named": [
                {
                    "slug": "stefano",
                    "display_name": "Stefano",
                    "presence_method": "quorum",
                    "sources": [
                        "binary_sensor.phone_wifi",
                        "binary_sensor.watch_ble",
                    ],
                    "required": 2,
                    "enable_override": False,
                }
            ]
        }
    )
    entry.add_to_hass(hass)

    hass.states.async_set("binary_sensor.phone_wifi", "off")
    hass.states.async_set("binary_sensor.watch_ble", "off")
    await hass.async_block_till_done()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.heima_person_stefano_home") is not None
    assert hass.states.get("binary_sensor.heima_person_stefano_home").state == "off"
    assert hass.states.get("sensor.heima_person_stefano_source").state == "quorum"

    hass.states.async_set("binary_sensor.phone_wifi", "on")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.heima_person_stefano_home").state == "off"

    hass.states.async_set("binary_sensor.watch_ble", "on")
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.heima_person_stefano_home").state == "on"
    assert hass.states.get("sensor.heima_person_stefano_confidence").state == "100"

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    trace = coordinator.engine.diagnostics()["presence"]["group_trace"]["person:stefano"]
    assert trace["plugin_id"] == "builtin.quorum"
    assert trace["required"] == 2
    assert trace["active_count"] == 2
    assert trace["used_plugin_fallback"] is False
    assert trace["fused_observation"]["state"] == "on"


@pytest.mark.asyncio
async def test_e2e_person_weighted_quorum_uses_weights_and_group_trace(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    entry = _entry(
        {
            "people_named": [
                {
                    "slug": "stefano",
                    "display_name": "Stefano",
                    "presence_method": "quorum",
                    "sources": [
                        "binary_sensor.phone_wifi",
                        "binary_sensor.watch_ble",
                    ],
                    "group_strategy": "weighted_quorum",
                    "weight_threshold": 1.2,
                    "source_weights": {
                        "binary_sensor.phone_wifi": 0.4,
                        "binary_sensor.watch_ble": 0.8,
                    },
                    "required": 1,
                    "enable_override": False,
                }
            ]
        }
    )
    entry.add_to_hass(hass)

    hass.states.async_set("binary_sensor.phone_wifi", "on")
    hass.states.async_set("binary_sensor.watch_ble", "off")
    await hass.async_block_till_done()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.heima_person_stefano_home").state == "off"

    hass.states.async_set("binary_sensor.watch_ble", "on")
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.heima_person_stefano_home").state == "on"
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    trace = coordinator.engine.diagnostics()["presence"]["group_trace"]["person:stefano"]
    assert trace["plugin_id"] == "builtin.weighted_quorum"
    assert trace["group_strategy"] == "weighted_quorum"
    assert trace["weight_threshold"] == 1.2
    assert trace["configured_source_weights"]["binary_sensor.watch_ble"] == 0.8


@pytest.mark.asyncio
async def test_e2e_anonymous_presence_updates_sensor_and_group_trace(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    entry = _entry(
        {
            "people_anonymous": {
                "enabled": True,
                "sources": [
                    "binary_sensor.motion_hall",
                    "binary_sensor.motion_living",
                ],
                "required": 1,
                "anonymous_count_weight": 2,
            }
        }
    )
    entry.add_to_hass(hass)

    hass.states.async_set("binary_sensor.motion_hall", "off")
    hass.states.async_set("binary_sensor.motion_living", "off")
    await hass.async_block_till_done()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.heima_anonymous_presence") is not None
    assert hass.states.get("binary_sensor.heima_anonymous_presence").state == "off"
    assert _anon_source_state(hass) is not None
    assert _anon_source_state(hass).state == (
        "binary_sensor.motion_hall,binary_sensor.motion_living"
    )

    hass.states.async_set("binary_sensor.motion_hall", "on")
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.heima_anonymous_presence").state == "on"
    assert hass.states.get("sensor.heima_people_count").state == "2"
    assert hass.states.get("sensor.heima_people_home_list").state == "anonymous"

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    trace = coordinator.engine.diagnostics()["presence"]["group_trace"]["anonymous"]
    assert trace["plugin_id"] == "builtin.quorum"
    assert trace["required"] == 1
    assert trace["active_count"] == 1
    assert trace["used_plugin_fallback"] is False
    assert trace["fused_observation"]["state"] == "on"


@pytest.mark.asyncio
async def test_e2e_anonymous_weighted_quorum_uses_weights_and_group_trace(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    entry = _entry(
        {
            "people_anonymous": {
                "enabled": True,
                "sources": [
                    "binary_sensor.motion_hall",
                    "binary_sensor.motion_living",
                ],
                "group_strategy": "weighted_quorum",
                "weight_threshold": 1.2,
                "source_weights": {
                    "binary_sensor.motion_hall": 0.4,
                    "binary_sensor.motion_living": 0.8,
                },
                "required": 1,
                "anonymous_count_weight": 2,
            }
        }
    )
    entry.add_to_hass(hass)

    hass.states.async_set("binary_sensor.motion_hall", "on")
    hass.states.async_set("binary_sensor.motion_living", "off")
    await hass.async_block_till_done()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.heima_anonymous_presence").state == "off"

    hass.states.async_set("binary_sensor.motion_living", "on")
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.heima_anonymous_presence").state == "on"
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    trace = coordinator.engine.diagnostics()["presence"]["group_trace"]["anonymous"]
    assert trace["plugin_id"] == "builtin.weighted_quorum"
    assert trace["group_strategy"] == "weighted_quorum"
    assert trace["weight_threshold"] == 1.2
    assert trace["configured_source_weights"]["binary_sensor.motion_living"] == 0.8


@pytest.mark.asyncio
async def test_e2e_room_occupancy_plugin_failure_uses_fail_safe_off_fallback(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    entry = _entry(
        {
            "rooms": [
                {
                    "room_id": "studio",
                    "occupancy_mode": "derived",
                    "sources": ["binary_sensor.room_presence"],
                    "logic": "any_of",
                    "on_dwell_s": 0,
                    "off_dwell_s": 0,
                }
            ]
        }
    )
    entry.add_to_hass(hass)

    hass.states.async_set("binary_sensor.room_presence", "on")
    await hass.async_block_till_done()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    registry = NormalizationFusionRegistry()
    registry.register(_ExplodingAnyOfPlugin())
    coordinator.engine._normalizer = InputNormalizer(hass, fusion_registry=registry)

    await coordinator.async_request_evaluation(reason="test:plugin_failure")
    await hass.async_block_till_done()

    assert _room_entity_state(hass, "studio").state == "off"
    trace = coordinator.engine.diagnostics()["occupancy"]["room_trace"]["studio"]
    assert trace["plugin_id"] == "builtin.any_of"
    assert trace["used_plugin_fallback"] is True
    assert trace["fused_observation"]["state"] == "off"
    assert trace["fused_observation"]["reason"] == "plugin_error_fallback"
    assert trace["fused_observation"]["evidence"]["fallback"] == "off"


@pytest.mark.asyncio
async def test_e2e_security_smart_uses_boolean_plugin_corroboration_trace(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    entry = _entry(
        {
            "people_named": [
                {
                    "slug": "stefano",
                    "presence_method": "manual",
                    "enable_override": True,
                }
            ],
            "rooms": [
                {
                    "room_id": "studio",
                    "occupancy_mode": "derived",
                    "sources": ["binary_sensor.room_presence"],
                    "logic": "any_of",
                    "on_dwell_s": 0,
                    "off_dwell_s": 0,
                }
            ],
            "security": {
                "enabled": True,
                "security_state_entity": "alarm_control_panel.home",
                "armed_away_value": "armed_away",
                "armed_home_value": "armed_home",
            },
            "notifications": {
                "enabled_event_categories": ["security", "people"],
                "security_mismatch_policy": "smart",
                "security_mismatch_persist_s": 0,
            },
        }
    )
    entry.add_to_hass(hass)

    hass.states.async_set("alarm_control_panel.home", "armed_away")
    hass.states.async_set("binary_sensor.room_presence", "on")
    await hass.async_block_till_done()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    coordinator.engine.state.set_select("heima_person_stefano_override", "force_home")
    await coordinator.async_request_evaluation(reason="test:security_corroboration")
    await hass.async_block_till_done()

    trace = coordinator.engine.diagnostics()["security"]["corroboration_trace"]
    assert trace["plugin_id"] == "builtin.any_of"
    assert trace["used_plugin_fallback"] is False
    assert trace["fused_observation"]["state"] == "on"


@pytest.mark.asyncio
async def test_e2e_house_signal_helpers_use_boolean_plugin_trace(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    entry = _entry({})
    entry.add_to_hass(hass)

    hass.states.async_set("binary_sensor.relax_mode", "on")
    await hass.async_block_till_done()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    trace = coordinator.engine.diagnostics()["house_signals"]["trace"]["relax_mode"]
    assert trace["plugin_id"] == "builtin.any_of"
    assert trace["used_plugin_fallback"] is False
    assert trace["fused_observation"]["state"] == "on"


@pytest.mark.asyncio
async def test_e2e_heating_fixed_target_branch_updates_canonical_entities_and_calls_climate(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    calls: list[dict[str, object]] = []

    async def _capture_climate_call(call):
        calls.append(dict(call.data))

    hass.services.async_register("climate", "set_temperature", _capture_climate_call)

    entry = _entry(
        {
            "heating": {
                "climate_entity": "climate.test_thermostat",
                "apply_mode": "set_temperature",
                "temperature_step": 0.5,
                "manual_override_guard": True,
                "override_branches": {
                    "away": {
                        "branch": "fixed_target",
                        "target_temperature": 20.0,
                    }
                },
            }
        }
    )
    entry.add_to_hass(hass)

    hass.states.async_set("climate.test_thermostat", "heat", {"temperature": 18.0})
    await hass.async_block_till_done()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.heima_heating_state") is not None
    assert hass.states.get("sensor.heima_heating_state").state == "target_active"
    assert hass.states.get("sensor.heima_heating_reason").state == "fixed_target_branch"
    assert hass.states.get("sensor.heima_heating_phase").state == "fixed_target"
    assert hass.states.get("sensor.heima_heating_branch").state == "fixed_target"
    assert float(hass.states.get("sensor.heima_heating_target_temp").state) == 20.0
    assert float(hass.states.get("sensor.heima_heating_current_setpoint").state) == 18.0

    assert calls == [
        {
            "entity_id": "climate.test_thermostat",
            "hvac_mode": "heat",
            "temperature": 20.0,
        }
    ]

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    trace = coordinator.engine.diagnostics()["heating"]
    assert trace["selected_branch"] == "fixed_target"
    assert trace["apply_allowed"] is True
    assert float(hass.states.get("sensor.heima_heating_last_applied_target").state) == 20.0


@pytest.mark.asyncio
async def test_e2e_heating_vacation_curve_branch_computes_and_applies_target(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    calls: list[dict[str, object]] = []

    async def _capture_climate_call(call):
        calls.append(dict(call.data))

    hass.services.async_register("climate", "set_temperature", _capture_climate_call)

    entry = _entry(
        {
            "heating": {
                "climate_entity": "climate.test_thermostat",
                "apply_mode": "set_temperature",
                "temperature_step": 0.5,
                "manual_override_guard": True,
                "outdoor_temperature_entity": "sensor.outdoor_temp",
                "vacation_hours_from_start_entity": "sensor.vacation_from",
                "vacation_hours_to_end_entity": "sensor.vacation_to",
                "vacation_total_hours_entity": "sensor.vacation_total",
                "vacation_is_long_entity": "binary_sensor.vacation_long",
                "override_branches": {
                    "vacation": {
                        "branch": "vacation_curve",
                        "vacation_ramp_down_h": 8.0,
                        "vacation_ramp_up_h": 10.0,
                        "vacation_min_temp": 16.5,
                        "vacation_comfort_temp": 19.5,
                        "vacation_start_temp": 19.5,
                        "vacation_min_total_hours_for_ramp": 24.0,
                    }
                },
            }
        }
    )
    entry.add_to_hass(hass)

    hass.states.async_set("input_boolean.vacation_mode", "on")
    hass.states.async_set("climate.test_thermostat", "heat", {"temperature": 18.0})
    hass.states.async_set("sensor.outdoor_temp", "5.0")
    hass.states.async_set("sensor.vacation_from", "2.0")
    hass.states.async_set("sensor.vacation_to", "30.0")
    hass.states.async_set("sensor.vacation_total", "32.0")
    hass.states.async_set("binary_sensor.vacation_long", "on")
    await hass.async_block_till_done()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.heima_house_state").state == "vacation"
    assert hass.states.get("sensor.heima_heating_state").state == "target_active"
    assert hass.states.get("sensor.heima_heating_reason").state == "vacation_curve_branch"
    assert hass.states.get("sensor.heima_heating_phase").state == "ramp_down"
    assert hass.states.get("sensor.heima_heating_branch").state == "vacation_curve"
    assert float(hass.states.get("sensor.heima_heating_target_temp").state) == 19.0

    assert calls == [
        {
            "entity_id": "climate.test_thermostat",
            "hvac_mode": "heat",
            "temperature": 19.0,
        }
    ]

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    trace = coordinator.engine.diagnostics()["heating"]
    assert trace["selected_branch"] == "vacation_curve"
    assert trace["vacation"]["is_long"] is True
    assert trace["vacation"]["quantized_target"] == 19.0


@pytest.mark.asyncio
async def test_e2e_heating_fixed_target_small_delta_skips_apply_and_sets_guard(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    calls: list[dict[str, object]] = []

    async def _capture_climate_call(call):
        calls.append(dict(call.data))

    hass.services.async_register("climate", "set_temperature", _capture_climate_call)

    entry = _entry(
        {
            "heating": {
                "climate_entity": "climate.test_thermostat",
                "apply_mode": "set_temperature",
                "temperature_step": 0.5,
                "manual_override_guard": True,
                "override_branches": {
                    "away": {
                        "branch": "fixed_target",
                        "target_temperature": 20.0,
                    }
                },
            }
        }
    )
    entry.add_to_hass(hass)

    hass.states.async_set("climate.test_thermostat", "heat", {"temperature": 19.8})
    await hass.async_block_till_done()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.heima_heating_state").state == "idle"
    assert hass.states.get("sensor.heima_heating_reason").state == "small_delta_skip"
    assert hass.states.get("binary_sensor.heima_heating_applying_guard").state == "on"
    assert calls == []


@pytest.mark.asyncio
async def test_e2e_heating_manual_hold_blocks_fixed_target_apply(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    calls: list[dict[str, object]] = []

    async def _capture_climate_call(call):
        calls.append(dict(call.data))

    hass.services.async_register("climate", "set_temperature", _capture_climate_call)

    entry = _entry(
        {
            "heating": {
                "climate_entity": "climate.test_thermostat",
                "apply_mode": "set_temperature",
                "temperature_step": 0.5,
                "manual_override_guard": True,
                "override_branches": {
                    "away": {
                        "branch": "fixed_target",
                        "target_temperature": 20.0,
                    }
                },
            }
        }
    )
    entry.add_to_hass(hass)

    hass.states.async_set("climate.test_thermostat", "heat", {"temperature": 18.0})
    await hass.async_block_till_done()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    coordinator.engine.state.set_binary("heima_heating_manual_hold", True)
    await coordinator.async_request_evaluation(reason="test:heating_manual_hold")
    await hass.async_block_till_done()

    assert hass.states.get("sensor.heima_heating_state").state == "blocked"
    assert hass.states.get("sensor.heima_heating_reason").state == "manual_override_blocked"
    assert hass.states.get("binary_sensor.heima_heating_applying_guard").state == "on"
    assert len(calls) == 1
    # Check the canonical event sensor as the stable integration-level observable.
    assert hass.states.get("sensor.heima_last_event").state == "heating.manual_override_blocked"
