from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heima.const import DOMAIN


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
