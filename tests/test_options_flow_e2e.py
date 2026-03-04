from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.heima.config_flow import HeimaOptionsFlowHandler


def _fake_hass():
    return SimpleNamespace(
        services=SimpleNamespace(async_services=lambda: {"notify": {}}),
        config=SimpleNamespace(time_zone="Europe/Rome", language="it"),
    )


def _flow(options: dict | None = None) -> HeimaOptionsFlowHandler:
    flow = HeimaOptionsFlowHandler(SimpleNamespace(options=options or {}))
    flow.hass = _fake_hass()
    return flow


@pytest.mark.asyncio
async def test_rooms_flow_persists_actuation_only_room_with_save_and_close():
    flow = _flow()

    result = await flow.async_step_rooms_add(
        {
            "room_id": "soggiorno",
            "display_name": "Soggiorno",
            "area_id": "soggiorno",
            "occupancy_mode": "none",
            "sources": [],
            "logic": "any_of",
            "on_dwell_s": 5,
            "off_dwell_s": 120,
            "max_on_s": None,
        }
    )
    assert result["type"] == "menu"

    saved = await flow.async_step_rooms_save()
    assert saved["type"] == "create_entry"
    room = saved["data"]["rooms"][0]
    assert room["room_id"] == "soggiorno"
    assert room["occupancy_mode"] == "none"
    assert room["sources"] == []


@pytest.mark.asyncio
async def test_general_flow_persists_house_signal_bindings():
    flow = _flow()

    result = await flow.async_step_general(
        {
            "engine_enabled": True,
            "timezone": "Europe/Rome",
            "language": "it",
            "lighting_apply_mode": "scene",
            "vacation_mode_entity": "input_boolean.vacation_mode",
            "guest_mode_entity": "",
            "sleep_window_entity": "binary_sensor.sleep_window",
            "relax_mode_entity": "binary_sensor.relax_mode",
            "work_window_entity": "binary_sensor.work_window",
        }
    )
    assert result["type"] == "menu"
    assert flow.options["house_signals"] == {
        "vacation_mode": "input_boolean.vacation_mode",
        "sleep_window": "binary_sensor.sleep_window",
        "relax_mode": "binary_sensor.relax_mode",
        "work_window": "binary_sensor.work_window",
    }


@pytest.mark.asyncio
async def test_lighting_room_edit_flow_can_clear_scenes_and_persist_on_save():
    flow = _flow(
        {
            "rooms": [
                {
                    "room_id": "soggiorno",
                    "display_name": "Soggiorno",
                    "area_id": "soggiorno",
                    "occupancy_mode": "none",
                    "sources": [],
                    "logic": "any_of",
                }
            ],
            "lighting_rooms": [
                {
                    "room_id": "soggiorno",
                    "scene_evening": "scene.lettura",
                    "scene_relax": "scene.relax",
                    "scene_night": "scene.night",
                    "scene_off": "scene.off",
                    "enable_manual_hold": True,
                }
            ],
        }
    )

    selected = await flow.async_step_lighting_rooms_edit({"room": "soggiorno"})
    assert selected["type"] == "form"
    assert selected["step_id"] == "lighting_rooms_edit_form"

    edited = await flow.async_step_lighting_rooms_edit_form(
        {
            "room_id": "soggiorno",
            "enable_manual_hold": True,
        }
    )
    assert edited["type"] == "menu"

    saved = await flow.async_step_lighting_rooms_save()
    assert saved["type"] == "create_entry"
    room_map = saved["data"]["lighting_rooms"][0]
    assert room_map["room_id"] == "soggiorno"
    assert room_map["enable_manual_hold"] is True
    assert "scene_evening" not in room_map
    assert "scene_relax" not in room_map
    assert "scene_night" not in room_map
    assert "scene_off" not in room_map


@pytest.mark.asyncio
async def test_rooms_flow_persists_weighted_quorum_room_source_weights():
    flow = _flow()

    result = await flow.async_step_rooms_add(
        {
            "room_id": "studio",
            "display_name": "Studio",
            "area_id": "studio",
            "occupancy_mode": "derived",
            "sources": ["binary_sensor.motion", "binary_sensor.mmwave"],
            "logic": "weighted_quorum",
            "weight_threshold": 1.2,
            "source_weights": "binary_sensor.motion=0.4\nbinary_sensor.mmwave=0.8",
            "on_dwell_s": 5,
            "off_dwell_s": 120,
            "max_on_s": None,
        }
    )
    assert result["type"] == "menu"

    saved = await flow.async_step_rooms_save()
    assert saved["type"] == "create_entry"
    room = saved["data"]["rooms"][0]
    assert room["logic"] == "weighted_quorum"
    assert room["weight_threshold"] == 1.2
    assert room["source_weights"] == {
        "binary_sensor.motion": 0.4,
        "binary_sensor.mmwave": 0.8,
    }


@pytest.mark.asyncio
async def test_heating_flow_persists_general_config_and_branch_mapping():
    flow = _flow()

    result = await flow.async_step_heating(
        {
            "climate_entity": "climate.termostato",
            "apply_mode": "delegate_to_scheduler",
            "temperature_step": 0.5,
            "manual_override_guard": True,
            "outdoor_temperature_entity": "sensor.outdoor_temp",
            "vacation_hours_from_start_entity": "sensor.hours_from",
            "vacation_hours_to_end_entity": "sensor.hours_to",
            "vacation_total_hours_entity": "sensor.hours_total",
            "vacation_is_long_entity": "binary_sensor.vacation_is_long",
        }
    )
    assert result["type"] == "menu"
    assert result["step_id"] == "heating_branches_menu"

    selected = await flow.async_step_heating_branches_edit({"house_state": "vacation"})
    assert selected["type"] == "form"
    assert selected["step_id"] == "heating_branch_edit_form"

    updated = await flow.async_step_heating_branch_edit_form(
        {
            "house_state": "vacation",
            "branch": "vacation_curve",
            "vacation_ramp_down_h": 8,
            "vacation_ramp_up_h": 10,
            "vacation_min_temp": 16.5,
            "vacation_comfort_temp": 19.5,
            "vacation_start_temp": 19.5,
            "vacation_min_total_hours_for_ramp": 24,
        }
    )
    assert updated["type"] == "menu"
    assert updated["step_id"] == "heating_branches_menu"

    saved = await flow.async_step_heating_branches_save()
    assert saved["type"] == "form"
    assert saved["step_id"] == "security"

    heating = flow.options["heating"]
    assert heating["climate_entity"] == "climate.termostato"
    assert heating["temperature_step"] == 0.5
    assert heating["override_branches"]["vacation"]["branch"] == "vacation_curve"
    assert heating["override_branches"]["vacation"]["vacation_min_temp"] == 16.5
