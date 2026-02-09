"""Entity registry builders for Heima canonical entities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry

from ..const import (
    OPT_HEATING,
    OPT_LIGHTING_ROOMS,
    OPT_LIGHTING_ZONES,
    OPT_PEOPLE_ANON,
    OPT_PEOPLE_NAMED,
    OPT_ROOMS,
    OPT_SECURITY,
)

LIGHTING_INTENTS = ["auto", "off", "scene_evening", "scene_relax", "scene_night"]
HEATING_INTENTS = ["auto", "eco", "comfort", "preheat", "off"]
SECURITY_INTENTS = ["auto", "armed_away", "armed_home", "disarmed"]


@dataclass(frozen=True)
class HeimaEntityDescription:
    key: str
    name: str


@dataclass(frozen=True)
class HeimaSelectDescription(HeimaEntityDescription):
    options: list[str]


@dataclass(frozen=True)
class HeimaRegistry:
    sensors: list[HeimaEntityDescription]
    binary_sensors: list[HeimaEntityDescription]
    selects: list[HeimaSelectDescription]


def build_registry(entry: ConfigEntry) -> HeimaRegistry:
    options = dict(entry.options)

    sensors: list[HeimaEntityDescription] = []
    binaries: list[HeimaEntityDescription] = []
    selects: list[HeimaSelectDescription] = []

    # People (named)
    for person in options.get(OPT_PEOPLE_NAMED, []):
        slug = person.get("slug")
        label = _label(person.get("display_name") or slug)
        if not slug:
            continue
        binaries.append(_b(_k(f"heima_person_{slug}_home"), f"Heima Person {label} Home"))
        sensors.append(_s(_k(f"heima_person_{slug}_confidence"), f"Heima Person {label} Confidence"))
        sensors.append(_s(_k(f"heima_person_{slug}_source"), f"Heima Person {label} Source"))
        if person.get("enable_override"):
            selects.append(
                _sel(
                    _k(f"heima_person_{slug}_override"),
                    f"Heima Person {label} Override",
                    ["auto", "force_home", "force_away"],
                )
            )

    binaries.append(_b(_k("heima_anyone_home"), "Heima Anyone Home"))
    sensors.append(_s(_k("heima_people_count"), "Heima People Count"))
    sensors.append(_s(_k("heima_people_home_list"), "Heima People Home List"))

    # People (anonymous)
    anon = options.get(OPT_PEOPLE_ANON, {})
    if anon.get("enabled"):
        binaries.append(_b(_k("heima_anonymous_presence"), "Heima Anonymous Presence"))
        sensors.append(_s(_k("heima_anonymous_presence_confidence"), "Heima Anonymous Confidence"))
        sensors.append(_s(_k("heima_anonymous_presence_source"), "Heima Anonymous Source"))

    # Occupancy (rooms)
    for room in options.get(OPT_ROOMS, []):
        room_id = room.get("room_id")
        label = _label(room.get("display_name") or room_id)
        if not room_id:
            continue
        binaries.append(_b(_k(f"heima_occ_{room_id}"), f"Heima Occupancy {label}"))
        sensors.append(_s(_k(f"heima_occ_{room_id}_source"), f"Heima Occupancy {label} Source"))
        sensors.append(_s(_k(f"heima_occ_{room_id}_last_change"), f"Heima Occupancy {label} Last Change"))

    # Occupancy (zones) - derived from lighting zones
    for zone in options.get(OPT_LIGHTING_ZONES, []):
        zone_id = zone.get("zone_id")
        label = _label(zone.get("display_name") or zone_id)
        if not zone_id:
            continue
        binaries.append(_b(_k(f"heima_occ_zone_{zone_id}"), f"Heima Occupancy Zone {label}"))

    # House state
    sensors.append(_s(_k("heima_house_state"), "Heima House State"))
    sensors.append(_s(_k("heima_house_state_reason"), "Heima House State Reason"))

    # Lighting
    for zone in options.get(OPT_LIGHTING_ZONES, []):
        zone_id = zone.get("zone_id")
        label = _label(zone.get("display_name") or zone_id)
        if not zone_id:
            continue
        selects.append(
            _sel(
                _k(f"heima_lighting_intent_{zone_id}"),
                f"Heima Lighting Intent {label}",
                LIGHTING_INTENTS,
            )
        )

    for room in options.get(OPT_LIGHTING_ROOMS, []):
        room_id = room.get("room_id")
        label = _label(room.get("room_id"))
        if not room_id:
            continue
        if room.get("enable_manual_hold", True):
            binaries.append(
                _b(_k(f"heima_lighting_manual_hold_{room_id}"), f"Heima Lighting Hold {label}")
            )

    # Heating
    heating = options.get(OPT_HEATING, {})
    if heating:
        selects.append(
            _sel(_k("heima_heating_intent"), "Heima Heating Intent", HEATING_INTENTS)
        )
        binaries.append(_b(_k("heima_heating_manual_hold"), "Heima Heating Manual Hold"))
        binaries.append(_b(_k("heima_heating_applying_guard"), "Heima Heating Applying Guard"))

    # Security
    security = options.get(OPT_SECURITY, {})
    if security:
        selects.append(
            _sel(_k("heima_security_intent"), "Heima Security Intent", SECURITY_INTENTS)
        )
        sensors.append(_s(_k("heima_security_state"), "Heima Security State"))
        sensors.append(_s(_k("heima_security_reason"), "Heima Security Reason"))

    # Notifications
    sensors.append(_s(_k("heima_last_event"), "Heima Last Event"))
    sensors.append(_s(_k("heima_event_stats"), "Heima Event Stats"))

    return HeimaRegistry(sensors=sensors, binary_sensors=binaries, selects=selects)


def _label(value: str | None) -> str:
    if not value:
        return "Unknown"
    return str(value).replace("_", " ").title()


def _k(key: str) -> str:
    return key if key.startswith("heima_") else f"heima_{key}"


def _s(key: str, name: str) -> HeimaEntityDescription:
    return HeimaEntityDescription(key=key, name=name)


def _b(key: str, name: str) -> HeimaEntityDescription:
    return HeimaEntityDescription(key=key, name=name)


def _sel(key: str, name: str, options: list[str]) -> HeimaSelectDescription:
    return HeimaSelectDescription(key=key, name=name, options=options)
