"""The Tody integration: read-only mirror of Tody cleaning tasks as to-do lists."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DEVICE_NAME, DOMAIN
from .coordinator import TodyConfigEntry, TodyCoordinator

PLATFORMS: list[Platform] = [Platform.TODO, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: TodyConfigEntry) -> bool:
    """Set up Tody from a config entry."""
    coordinator = TodyCoordinator(hass, entry)
    # Created up front so the per-area devices can reference it (via_device_id).
    coordinator.main_device_id = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.masterdata_id)},
        entry_type=dr.DeviceEntryType.SERVICE,
        name=DEVICE_NAME,
        manufacturer="Looploop",
        model="Tody",
    ).id
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TodyConfigEntry) -> bool:
    """Unload a config entry. Tody itself is left untouched."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
