"""Links between Tody areas and Home Assistant areas.

The link is stored as the Home Assistant area of each Tody area's to-do list entity, so
HA's own entity settings and this integration's options always show the same thing.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er

from .const import DOMAIN


def area_list_entity_id(hass: HomeAssistant, masterdata_id: str, tody_area_id: str) -> str | None:
    """Entity id of the to-do list for a Tody area, if it exists."""
    return er.async_get(hass).async_get_entity_id("todo", DOMAIN, f"{masterdata_id}_area_{tody_area_id}")


def linked_ha_area(hass: HomeAssistant, masterdata_id: str, tody_area_id: str) -> str | None:
    """Home Assistant area id linked to a Tody area."""
    registry = er.async_get(hass)
    if (entity_id := area_list_entity_id(hass, masterdata_id, tody_area_id)) is None:
        return None
    entry = registry.async_get(entity_id)
    return entry.area_id if entry else None


def link_ha_area(hass: HomeAssistant, masterdata_id: str, tody_area_id: str, ha_area_id: str | None) -> None:
    """Link (or with None: unlink) a Tody area to a Home Assistant area."""
    if (entity_id := area_list_entity_id(hass, masterdata_id, tody_area_id)) is not None:
        er.async_get(hass).async_update_entity(entity_id, area_id=ha_area_id)


def match_ha_area(hass: HomeAssistant, name: str) -> str | None:
    """Home Assistant area with the same name (or alias) as a Tody area."""
    registry = ar.async_get(hass)
    if area := registry.async_get_area_by_name(name):
        return area.id
    wanted = name.strip().casefold()
    for area in registry.async_list_areas():
        if any(alias.strip().casefold() == wanted for alias in area.aliases):
            return area.id
    return None
