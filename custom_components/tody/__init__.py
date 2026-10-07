"""The Tody integration: read-only mirror of Tody cleaning tasks as to-do lists."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import TodyConfigEntry, TodyCoordinator

PLATFORMS: list[Platform] = [Platform.TODO, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: TodyConfigEntry) -> bool:
    """Set up Tody from a config entry."""
    coordinator = TodyCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TodyConfigEntry) -> bool:
    """Unload a config entry. Tody itself is left untouched."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
