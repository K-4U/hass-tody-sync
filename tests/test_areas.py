"""Tody areas as devices in Home Assistant areas."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tody.const import DOMAIN

from .conftest import MASTERDATA_ID


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    hass.config.language = "en"
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _area_device(hass: HomeAssistant, tody_area_id: str, entry_id: str) -> dr.DeviceEntry:
    return dr.async_get(hass).async_get_device_by_identifier((DOMAIN, f"{MASTERDATA_ID}_area_{tody_area_id}"), entry_id)


async def test_area_devices_land_in_suggested_area(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """New area devices go to the HA area with the Tody area's name; entities themselves get no area."""
    kitchen = ar.async_get(hass).async_create("Kitchen")
    await _setup(hass, config_entry)

    assert _area_device(hass, "a-kitchen", config_entry.entry_id).area_id == kitchen.id
    # Standard HA behaviour: a suggested area that doesn't exist yet is created.
    assert _area_device(hass, "a-bath", config_entry.entry_id).area_id == ar.async_get(hass).async_get_area_by_name("Bathroom").id
    assert er.async_get(hass).async_get("todo.kitchen_tody_tasks").area_id is None


async def test_user_area_choice_is_kept(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """After creation the user owns the device's area; a reload doesn't move it back."""
    await _setup(hass, config_entry)
    office = ar.async_get(hass).async_create("Office")
    dr.async_get(hass).async_update_device(_area_device(hass, "a-kitchen", config_entry.entry_id).id, area_id=office.id)

    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert _area_device(hass, "a-kitchen", config_entry.entry_id).area_id == office.id


async def test_sensor_exposes_ha_areas(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Sensor items carry the HA area of their Tody area and follow device and entity area changes."""
    await _setup(hass, config_entry)
    areas = ar.async_get(hass)
    kitchen = areas.async_get_area_by_name("Kitchen")

    def items() -> list[dict]:
        return hass.states.get("sensor.tody_overdue_tasks").attributes["items"]

    kitchen_items = [i for i in items() if i["area"] == "Kitchen"]
    assert kitchen_items and all(i["ha_area"] == kitchen.id for i in kitchen_items)
    assert set(items()[0]) == {"name", "area", "ha_area", "due"}

    # Moving the device updates the attributes right away.
    office = areas.async_create("Office")
    dr.async_get(hass).async_update_device(_area_device(hass, "a-kitchen", config_entry.entry_id).id, area_id=office.id)
    await hass.async_block_till_done()
    assert all(i["ha_area"] == office.id for i in items() if i["area"] == "Kitchen")
    assert office.id in hass.states.get("sensor.tody_overdue_tasks").attributes["ha_areas"]

    # A user override on the list entity wins over the device's area.
    hall = areas.async_create("Hall")
    er.async_get(hass).async_update_entity("todo.kitchen_tody_tasks", area_id=hall.id)
    await hass.async_block_till_done()
    assert all(i["ha_area"] == hall.id for i in items() if i["area"] == "Kitchen")
