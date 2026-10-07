"""Linking Tody areas to Home Assistant areas."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tody.const import CONF_LOOKAHEAD_DAYS, CONF_SCAN_INTERVAL


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    hass.config.language = "en"
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _area_of(hass: HomeAssistant, entity_id: str) -> str | None:
    return er.async_get(hass).async_get(entity_id).area_id


async def test_new_area_lists_are_matched_by_name_once(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A Tody area list lands in the HA area with the same name; an unlink is not undone later."""
    kitchen = ar.async_get(hass).async_create("kitchen")  # case-insensitive match with "Kitchen"
    await _setup(hass, config_entry)

    assert _area_of(hass, "todo.tody_kitchen") == kitchen.id
    assert _area_of(hass, "todo.tody_bathroom") is None  # no HA area called Bathroom
    assert sorted(config_entry.options["auto_linked_areas"]) == ["a-bath", "a-kitchen"]

    # The user unlinks the kitchen; a reload must not link it again.
    er.async_get(hass).async_update_entity("todo.tody_kitchen", area_id=None)
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert _area_of(hass, "todo.tody_kitchen") is None


async def test_options_link_and_unlink(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """The options step writes the chosen HA area to each area list."""
    areas = ar.async_get(hass)
    kitchen = areas.async_create("Kitchen")
    bathroom = areas.async_create("Downstairs bathroom")
    await _setup(hass, config_entry)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_LOOKAHEAD_DAYS: 1, CONF_SCAN_INTERVAL: 15}
    )
    assert result["step_id"] == "areas"
    # Fields are the Tody area names, pre-filled with the current links.
    suggested = {str(key): key.description.get("suggested_value") for key in result["data_schema"].schema}
    assert suggested == {"Bathroom": None, "Kitchen": kitchen.id}

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"Bathroom": bathroom.id})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert _area_of(hass, "todo.tody_bathroom") == bathroom.id
    assert _area_of(hass, "todo.tody_kitchen") is None  # left empty -> unlinked


async def test_sensor_exposes_linked_ha_areas(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Overdue sensor lists per task its Tody area and linked HA area."""
    kitchen = ar.async_get(hass).async_create("Kitchen")
    await _setup(hass, config_entry)

    attrs = hass.states.get("sensor.tody_overdue_tasks").attributes
    assert attrs["ha_areas"] == ([kitchen.id] if any(i["area"] == "Kitchen" for i in attrs["items"]) else [])
    for item in attrs["items"]:
        assert item["ha_area"] == (kitchen.id if item["area"] == "Kitchen" else None)
        assert set(item) == {"name", "area", "ha_area", "due"}
