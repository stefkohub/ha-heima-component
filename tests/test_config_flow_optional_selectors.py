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


def test_notifications_payload_defaults_event_categories_when_missing():
    flow = _flow()
    normalized = flow._normalize_notifications_payload(
        {"routes": [], "dedup_window_s": 60, "rate_limit_per_key_s": 300}
    )
    assert set(normalized["enabled_event_categories"]) == {
        "people",
        "occupancy",
        "lighting",
        "heating",
        "security",
    }
    assert normalized["occupancy_mismatch_policy"] == "smart"
    assert normalized["occupancy_mismatch_min_derived_rooms"] == 2
    assert normalized["occupancy_mismatch_persist_s"] == 600
    assert normalized["security_mismatch_policy"] == "smart"
    assert normalized["security_mismatch_persist_s"] == 300


def test_notifications_payload_filters_invalid_event_categories():
    flow = _flow()
    normalized = flow._normalize_notifications_payload(
        {
            "routes": [],
            "enabled_event_categories": ["people", "system", "debug", "lighting"],
            "dedup_window_s": 60,
            "rate_limit_per_key_s": 300,
        }
    )
    assert normalized["enabled_event_categories"] == ["people", "lighting"]


def test_notifications_payload_normalizes_mismatch_policy():
    flow = _flow()
    normalized = flow._normalize_notifications_payload(
        {
            "routes": [],
            "enabled_event_categories": [],
            "occupancy_mismatch_policy": "invalid",
            "occupancy_mismatch_min_derived_rooms": 3,
            "occupancy_mismatch_persist_s": 120,
            "dedup_window_s": 60,
            "rate_limit_per_key_s": 300,
        }
    )
    assert normalized["occupancy_mismatch_policy"] == "smart"
    assert normalized["occupancy_mismatch_min_derived_rooms"] == 3
    assert normalized["occupancy_mismatch_persist_s"] == 120


def test_notifications_payload_normalizes_security_mismatch_policy():
    flow = _flow()
    normalized = flow._normalize_notifications_payload(
        {
            "routes": [],
            "enabled_event_categories": [],
            "security_mismatch_policy": "invalid",
            "security_mismatch_persist_s": 42,
            "dedup_window_s": 60,
            "rate_limit_per_key_s": 300,
        }
    )
    assert normalized["security_mismatch_policy"] == "smart"
    assert normalized["security_mismatch_persist_s"] == 42
