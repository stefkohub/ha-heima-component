from __future__ import annotations

from types import SimpleNamespace

from custom_components.heima.config_flow import HeimaOptionsFlowHandler


def _flow(options: dict | None = None) -> HeimaOptionsFlowHandler:
    return HeimaOptionsFlowHandler(SimpleNamespace(options=options or {}))


def test_lighting_room_schema_does_not_rehydrate_cleared_optional_scenes():
    flow = _flow()
    schema = flow._lighting_room_schema(
        {
            "room_id": "soggiorno",
            "scene_evening": "scene.evening_old",
            "scene_relax": "scene.relax_old",
            "enable_manual_hold": True,
        }
    )

    validated = schema({"room_id": "soggiorno", "enable_manual_hold": True})

    assert "scene_evening" not in validated
    assert "scene_relax" not in validated


def test_security_schema_does_not_rehydrate_cleared_optional_entity():
    flow = _flow()
    schema = flow._security_schema(
        {
            "enabled": True,
            "security_state_entity": "alarm_control_panel.home",
            "armed_away_value": "armed_away",
            "armed_home_value": "armed_home",
        }
    )

    validated = schema(
        {
            "enabled": True,
            "armed_away_value": "armed_away",
            "armed_home_value": "armed_home",
        }
    )

    assert "security_state_entity" not in validated


def test_notifications_schema_does_not_rehydrate_cleared_routes():
    flow = _flow()
    flow.hass = SimpleNamespace(
        services=SimpleNamespace(async_services=lambda: {"notify": {"mobile_app_x": object()}})
    )
    schema = flow._notifications_schema(
        {
            "routes": ["mobile_app_x"],
            "dedup_window_s": 60,
            "rate_limit_per_key_s": 300,
        }
    )

    validated = schema({"dedup_window_s": 0, "rate_limit_per_key_s": 0})

    assert "routes" not in validated
    assert validated["dedup_window_s"] == 0
    assert validated["rate_limit_per_key_s"] == 0
