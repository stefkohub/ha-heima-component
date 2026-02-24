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
