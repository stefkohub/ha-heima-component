"""Heima selects (canonical intents)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
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
    entities = [
        HeimaGenericSelect(coordinator, entry, desc.key, desc.name, desc.options)
        for desc in registry.selects
    ]
    async_add_entities(entities)


class HeimaGenericSelect(HeimaEntity, SelectEntity):
    """Generic canonical select."""

    def __init__(
        self,
        coordinator: HeimaCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        options: list[str],
    ) -> None:
        super().__init__(coordinator, entry)
        normalized_key = key if key.startswith("heima_") else f"heima_{key}"
        self._key = normalized_key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{normalized_key}"
        self._attr_suggested_object_id = normalized_key
        self._attr_options = options

    @property
    def current_option(self) -> str | None:
        return self.coordinator.engine.state.get_select(self._key)

    async def async_select_option(self, option: str) -> None:
        if option not in self._attr_options:
            return
        self.coordinator.engine.state.set_select(self._key, option)
        await self.coordinator.async_request_evaluation(
            reason=f"select_changed:{self._key}:{option}"
        )
