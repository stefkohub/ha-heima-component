from __future__ import annotations

from types import SimpleNamespace

from custom_components.heima.config_flow import HeimaOptionsFlowHandler


def _flow(options: dict | None = None) -> HeimaOptionsFlowHandler:
    return HeimaOptionsFlowHandler(SimpleNamespace(options=options or {}))


def test_room_validation_allows_sources_empty_when_occupancy_mode_none():
    flow = _flow()
    payload = {
        "room_id": "soggiorno",
        "display_name": "Soggiorno",
        "area_id": "soggiorno",
        "occupancy_mode": "none",
        "sources": [],
        "logic": "any_of",
    }
    assert flow._validate_room_payload(payload, is_edit=False) == {}


def test_room_validation_requires_sources_when_occupancy_mode_derived():
    flow = _flow()
    payload = {
        "room_id": "studio",
        "display_name": "Studio",
        "area_id": "studio",
        "occupancy_mode": "derived",
        "sources": [],
        "logic": "any_of",
    }
    errors = flow._validate_room_payload(payload, is_edit=False)
    assert errors == {"sources": "required"}


def test_finalize_options_backfills_empty_legacy_room_as_occupancy_none():
    flow = _flow(
        {
            "rooms": [
                {
                    "room_id": "soggiorno",
                    "display_name": "Soggiorno",
                    "area_id": "soggiorno",
                    "sources": [],
                    "logic": "any_of",
                }
            ]
        }
    )

    finalized = flow._finalize_options()

    assert finalized["rooms"][0]["occupancy_mode"] == "none"
