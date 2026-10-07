"""Which Home Assistant area a Tody area is in.

Each Tody area is its own device, created in the HA area of the same name (suggested_area).
After that the user owns the area assignment, as with any device.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN


def area_device_identifier(masterdata_id: str, tody_area_id: str) -> tuple[str, str]:
    """Device registry identifier of a Tody area's device."""
    return (DOMAIN, f"{masterdata_id}_area_{tody_area_id}")


def ha_area_of(hass: HomeAssistant, entry_id: str, masterdata_id: str, tody_area_id: str) -> str | None:
    """HA area of a Tody area: its list's own area if the user overrode it, else its device's area."""
    entity_id = er.async_get(hass).async_get_entity_id("todo", DOMAIN, f"{masterdata_id}_area_{tody_area_id}")
    if entity_id and (entity := er.async_get(hass).async_get(entity_id)) and entity.area_id:
        return entity.area_id
    device = dr.async_get(hass).async_get_device_by_identifier(
        area_device_identifier(masterdata_id, tody_area_id), entry_id
    )
    return device.area_id if device else None
