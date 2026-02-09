"""Heima binary sensors."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ..const import DOMAIN
from ..coordinator import HeimaCoordinator
from .base import HeimaEntity
from .registry import build_registry


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: HeimaCoordinator = data["coordinator"]
    registry = build_registry(entry)
    entities = [HeimaGenericBinarySensor(coordinator, entry, desc.key, desc.name) for desc in registry.binary_sensors]
    async_add_entities(entities)


class HeimaGenericBinarySensor(HeimaEntity, BinarySensorEntity):
    """Generic canonical binary sensor."""

    def __init__(self, coordinator: HeimaCoordinator, entry: ConfigEntry, key: str, name: str) -> None:
        super().__init__(coordinator, entry)
        normalized_key = key if key.startswith("heima_") else f"heima_{key}"
        self._key = normalized_key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{normalized_key}"
        self._attr_suggested_object_id = normalized_key

    @property
    def is_on(self):
        return self.coordinator.engine.state.get_binary(self._key)
