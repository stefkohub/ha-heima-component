from custom_components.heima.runtime.lighting import (
    pick_scene_for_intent,
    resolve_auto_intent,
    resolve_zone_intent,
)


def test_resolve_auto_intent_home_occupied():
    assert resolve_auto_intent("home", True) == "scene_evening"


def test_resolve_auto_intent_sleeping_occupied():
    assert resolve_auto_intent("sleeping", True) == "scene_night"


def test_resolve_auto_intent_unoccupied_forces_off():
    assert resolve_auto_intent("home", False) == "off"


def test_resolve_zone_intent_manual_respected_when_occupied():
    assert resolve_zone_intent("scene_relax", "home", True) == "scene_relax"


def test_resolve_zone_intent_manual_forced_off_when_unoccupied():
    assert resolve_zone_intent("scene_relax", "home", False) == "off"


def test_pick_scene_for_intent_fallback_relax_to_evening():
    room_map = {
        "scene_evening": "scene.living_evening",
        "scene_night": "scene.living_night",
    }
    assert pick_scene_for_intent(room_map, "scene_relax") == "scene.living_evening"


def test_pick_scene_for_intent_fallback_night_to_off():
    room_map = {
        "scene_off": "scene.living_off",
    }
    assert pick_scene_for_intent(room_map, "scene_night") == "scene.living_off"


def test_pick_scene_for_intent_off_without_mapping_is_none():
    assert pick_scene_for_intent({}, "off") is None
