"""Lighting domain helpers (policy + mapping)."""

from __future__ import annotations

from typing import Any

VALID_LIGHTING_INTENTS = {"auto", "off", "scene_evening", "scene_relax", "scene_night"}


def resolve_auto_intent(house_state: str, zone_occupied: bool) -> str:
    """Resolve auto intent to a concrete lighting intent."""
    if not zone_occupied:
        return "off"

    if house_state == "sleeping":
        return "scene_night"
    if house_state == "relax":
        return "scene_relax"
    if house_state in {"away", "vacation"}:
        return "off"
    if house_state in {"home", "working", "guest"}:
        return "scene_evening"
    return "off"


def resolve_zone_intent(requested_intent: str | None, house_state: str, zone_occupied: bool) -> str:
    """Resolve final zone intent from requested select state and context."""
    intent = (requested_intent or "auto").strip()
    if intent not in VALID_LIGHTING_INTENTS:
        intent = "auto"

    if intent == "auto":
        return resolve_auto_intent(house_state, zone_occupied)

    # Respect explicit intent, but still force off when zone is not occupied.
    if not zone_occupied:
        return "off"

    return intent


def pick_scene_for_intent(room_map: dict[str, Any], intent: str) -> str | None:
    """Select scene id for intent using v1 fallback rules."""
    direct = _scene(room_map, intent)
    if direct:
        return direct

    if intent == "scene_relax":
        return _scene(room_map, "scene_evening")

    if intent == "scene_evening":
        return _scene(room_map, "scene_relax")

    if intent == "scene_night":
        return _scene(room_map, "scene_evening") or _scene(room_map, "off")

    if intent == "off":
        return _scene(room_map, "off")

    return None


def _scene(room_map: dict[str, Any], intent: str) -> str | None:
    if intent == "off":
        return room_map.get("scene_off")
    return room_map.get(intent)
