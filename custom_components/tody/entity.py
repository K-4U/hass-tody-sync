"""Shared entity base for the Tody integration."""

from __future__ import annotations

from datetime import date, datetime

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DEVICE_NAME, DOMAIN
from .coordinator import TodyCoordinator


class TodyEntity(CoordinatorEntity[TodyCoordinator]):
    """Base entity: one service device per data sync, refreshed at local midnight."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TodyCoordinator, unique_suffix: str, device_info: DeviceInfo | None = None) -> None:
        """Initialize the entity; by default it belongs to the main Tody device."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.masterdata_id}_{unique_suffix}"
        self._attr_device_info = device_info or DeviceInfo(
            identifiers={(DOMAIN, coordinator.masterdata_id)},
            entry_type=DeviceEntryType.SERVICE,
            name=DEVICE_NAME,
            manufacturer="Looploop",
            model="Tody",
        )

    async def async_added_to_hass(self) -> None:
        """Also rewrite state just after local midnight so due/overdue flip without waiting for a poll."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(self.hass, self._async_midnight, hour=0, minute=0, second=5)
        )

    @callback
    def _async_midnight(self, _now: datetime) -> None:
        self.async_write_ha_state()


def today() -> date:
    """Today's local date."""
    return dt_util.now().date()
