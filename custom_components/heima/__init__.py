"""The Heima integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS
from .coordinator import HeimaCoordinator
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Heima (YAML not supported in v1)."""
    hass.data.setdefault(DOMAIN, {})

    if not hass.data[DOMAIN].get("services_registered"):
        await async_register_services(hass)
        hass.data[DOMAIN]["services_registered"] = True

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Heima from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = HeimaCoordinator(hass=hass, entry=entry)
    await coordinator.async_initialize()

    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    _LOGGER.info("Set up %s (entry_id=%s)", DOMAIN, entry.entry_id)
    return True


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update.

    Reloading the config entry is required to rebuild entity platforms because
    entity sets are derived from options (people, rooms, zones, etc.).
    """
    _LOGGER.debug("Options updated for %s, reloading entry %s", DOMAIN, entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data and "coordinator" in data:
            coordinator: HeimaCoordinator = data["coordinator"]
            await coordinator.async_shutdown()
    return unload_ok
