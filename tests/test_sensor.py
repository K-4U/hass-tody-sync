"""Tests for the Tody sensors."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import ATTR_STATE_CLASS, SensorStateClass
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import MASTERDATA_ID


@pytest.mark.usefixtures("setup_integration")
async def test_overdue_sensor(hass: HomeAssistant, entity_registry: er.EntityRegistry) -> None:
    """Overdue counts active tasks due before today."""
    state = hass.states.get("sensor.tody_overdue_tasks")
    assert state.state == "1"
    assert state.attributes[ATTR_STATE_CLASS] == SensorStateClass.MEASUREMENT
    assert ATTR_UNIT_OF_MEASUREMENT not in state.attributes
    assert state.attributes["areas"] == {"Kitchen": 1}
    assert state.attributes["tasks"] == ["Vacuum"]
    assert entity_registry.async_get("sensor.tody_overdue_tasks").unique_id == f"{MASTERDATA_ID}_overdue"


@pytest.mark.usefixtures("setup_integration")
async def test_due_today_sensor(hass: HomeAssistant, entity_registry: er.EntityRegistry) -> None:
    """Due today counts tasks due exactly today (not overdue, not tomorrow)."""
    state = hass.states.get("sensor.tody_due_today")
    assert state.state == "3"
    assert state.attributes["areas"] == {"Kitchen": 2, "Bathroom": 1}
    assert state.attributes["tasks"] == ["Dishes", "Hass task", "Mirror"]
    assert entity_registry.async_get("sensor.tody_due_today").unique_id == f"{MASTERDATA_ID}_due_today"


async def test_sensors_on_vacation(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_client: MagicMock, tody_data
) -> None:
    """Nothing is due during vacation."""
    mock_client.parse.return_value = replace(tody_data, on_vacation=True)
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.tody_overdue_tasks").state == "0"
    assert hass.states.get("sensor.tody_due_today").state == "0"
    assert hass.states.get("todo.tody_all_tasks").state == "0"
