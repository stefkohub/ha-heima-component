"""Base entities for Heima."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..coordinator import HeimaCoordinator


class HeimaEntity(CoordinatorEntity[HeimaCoordinator]):
    """Base class for Heima entities."""

    def __init__(self, coordinator: HeimaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self):
        return {
            "identifiers": {("heima", self._entry.entry_id)},
            "name": "Heima",
            "manufacturer": "Heima",
            "model": "Heima Engine",
        }
