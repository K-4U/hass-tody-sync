"""Tests for the Tody to-do lists."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN, TodoServices
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.tody.const import DOMAIN
from custom_components.tody.model import Area, Participant

from .conftest import ANNA, BOB, MASTERDATA_ID, OWN_PARTICIPANT, make_task, TODAY


async def _items(hass: HomeAssistant, entity_id: str) -> list[dict[str, Any]]:
    result = await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.GET_ITEMS,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
        return_response=True,
    )
    return result[entity_id]["items"]


async def _summaries(hass: HomeAssistant, entity_id: str) -> list[str]:
    return [item["summary"] for item in await _items(hass, entity_id)]


@pytest.mark.usefixtures("setup_integration")
async def test_entities_and_unique_ids(hass: HomeAssistant, entity_registry: er.EntityRegistry) -> None:
    """One list per area, an 'All tasks' list and one per participant except our own."""
    expected = {
        "todo.tody_all_tasks": f"{MASTERDATA_ID}_all",
        "todo.kitchen_tody_tasks": f"{MASTERDATA_ID}_area_a-kitchen",
        "todo.bathroom_tody_tasks": f"{MASTERDATA_ID}_area_a-bath",
        "todo.tody_anna": f"{MASTERDATA_ID}_person_{ANNA}",
        "todo.tody_bob": f"{MASTERDATA_ID}_person_{BOB}",
    }
    for entity_id, unique_id in expected.items():
        entry = entity_registry.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.unique_id == unique_id
    todo_entities = [e for e in entity_registry.entities.values() if e.domain == TODO_DOMAIN]
    assert len(todo_entities) == 5
    assert entity_registry.async_get_entity_id(TODO_DOMAIN, DOMAIN, f"{MASTERDATA_ID}_person_{OWN_PARTICIPANT}") is None


async def test_device(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """The main device holds All tasks, person lists and sensors; each Tody area is its own device."""
    device = device_registry.async_get_device_by_identifier((DOMAIN, MASTERDATA_ID), setup_integration.entry_id)
    assert device is not None
    assert device.name == "Tody"
    assert device.manufacturer == "Looploop"
    assert device.model == "Tody"
    assert device.entry_type is dr.DeviceEntryType.SERVICE
    assert len(er.async_entries_for_device(entity_registry, device.id)) == 5  # all, 2 people, 2 sensors

    kitchen = device_registry.async_get_device_by_identifier((DOMAIN, f"{MASTERDATA_ID}_area_a-kitchen"), setup_integration.entry_id)
    assert kitchen.name == "Tody tasks"
    assert kitchen.model == "Tody area"
    assert kitchen.via_device_id == device.id
    assert [e.entity_id for e in er.async_entries_for_device(entity_registry, kitchen.id)] == [
        "todo.kitchen_tody_tasks"
    ]
    state = hass.states.get("todo.kitchen_tody_tasks")
    assert state.attributes["friendly_name"] == "Tody tasks"
    assert state.attributes["tody_area"] == "Kitchen"


@pytest.mark.usefixtures("setup_integration")
async def test_items_per_list(hass: HomeAssistant) -> None:
    """Lists contain due tasks up to tomorrow, filtered by area/turn; paused and later tasks are hidden."""
    assert await _summaries(hass, "todo.tody_all_tasks") == ["Vacuum", "Dishes", "Hass task", "Mirror", "Toilet"]
    assert await _summaries(hass, "todo.kitchen_tody_tasks") == ["Vacuum", "Dishes", "Hass task"]
    assert await _summaries(hass, "todo.bathroom_tody_tasks") == ["Mirror", "Toilet"]
    assert await _summaries(hass, "todo.tody_anna") == ["Vacuum", "Mirror"]
    assert await _summaries(hass, "todo.tody_bob") == ["Dishes", "Mirror", "Toilet"]
    assert hass.states.get("todo.tody_all_tasks").state == "5"
    assert hass.states.get("todo.kitchen_tody_tasks").state == "3"


@pytest.mark.usefixtures("setup_integration")
async def test_item_fields(hass: HomeAssistant) -> None:
    """Items carry uid, a date-only due and a description; they're always needs_action."""
    items = await _items(hass, "todo.bathroom_tody_tasks")
    toilet = items[1]
    assert toilet["uid"] == "t-tomorrow"
    assert toilet["status"] == "needs_action"
    assert toilet["due"] == (TODAY + timedelta(days=1)).isoformat()
    assert toilet["description"]


@pytest.mark.usefixtures("setup_integration")
async def test_read_only(hass: HomeAssistant) -> None:
    """Lists support no write features and reject item changes."""
    state = hass.states.get("todo.tody_all_tasks")
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == 0
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            TODO_DOMAIN,
            TodoServices.ADD_ITEM,
            {ATTR_ENTITY_ID: "todo.tody_all_tasks", "item": "New"},
            blocking=True,
        )


async def test_lookahead_zero(hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: MagicMock) -> None:
    """With look-ahead 0 only overdue and today's tasks are shown."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={"lookahead_days": 0})
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert await _summaries(hass, "todo.bathroom_tody_tasks") == ["Mirror"]


async def test_dynamic_entities(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_client: MagicMock, tody_data
) -> None:
    """New areas/participants get lists on the next update; removed ones become unavailable."""
    coordinator = setup_integration.runtime_data
    new_data = replace(
        tody_data,
        areas={"a-kitchen": tody_data.areas["a-kitchen"], "a-garden": Area("a-garden", "Garden")},
        participants={**tody_data.participants, "p-cleo": Participant("p-cleo", "Cleo")},
        tasks={
            **tody_data.tasks,
            "t-garden": make_task("t-garden", "Mow", "a-garden", TODAY, {"p-cleo"}),
        },
    )
    mock_client.parse.return_value = new_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert await _summaries(hass, "todo.garden_tody_tasks") == ["Mow"]
    assert await _summaries(hass, "todo.tody_cleo") == ["Mow"]
    assert hass.states.get("todo.bathroom_tody_tasks").state == STATE_UNAVAILABLE
    assert hass.states.get("todo.kitchen_tody_tasks").state == "3"


async def test_midnight_refresh(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_client: MagicMock, freezer
) -> None:
    """At local midnight the lists are re-evaluated without waiting for a poll."""
    assert hass.states.get("todo.bathroom_tody_tasks").state == "2"
    # Stop polling so only the midnight timer can update state.
    setup_integration.runtime_data._unschedule_refresh()
    fetches = mock_client.fetch_snapshot.await_count
    # 00:00:05 PDT on Oct 9: "Windows" (due Oct 10) now falls within the 1-day look-ahead.
    freezer.move_to("2026-10-09 07:00:05+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("todo.bathroom_tody_tasks").state == "3"
    assert mock_client.fetch_snapshot.await_count == fetches
