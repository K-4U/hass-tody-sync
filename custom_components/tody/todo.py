"""Read-only to-do lists mirroring Tody tasks."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import model
from .const import CONF_LOOKAHEAD_DAYS, CONF_PARTICIPANT_ID, DEFAULT_LOOKAHEAD_DAYS
from .coordinator import TodyConfigEntry, TodyCoordinator
from .entity import TodyEntity, today
from .model import Task

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TodyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Tody to-do lists, adding new areas/participants as they appear."""
    coordinator = entry.runtime_data
    own_participant = entry.data[CONF_PARTICIPANT_ID]
    known_areas: set[str] = set()
    known_people: set[str] = set()

    async_add_entities([TodyAllTasksList(coordinator)])

    @callback
    def _async_add_new() -> None:
        data = coordinator.data
        if data is None:
            return
        new: list[TodyTodoList] = []
        for area_id in data.areas.keys() - known_areas:
            known_areas.add(area_id)
            new.append(TodyAreaList(coordinator, area_id))
        for participant_id in data.participants.keys() - known_people - {own_participant}:
            known_people.add(participant_id)
            new.append(TodyPersonList(coordinator, participant_id))
        if new:
            async_add_entities(new)

    _async_add_new()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new))


class TodyTodoList(TodyEntity, TodoListEntity):
    """A read-only to-do list of due Tody tasks."""

    _attr_supported_features = TodoListEntityFeature(0)

    def _include(self, task: Task) -> bool:
        raise NotImplementedError

    @property
    def todo_items(self) -> list[TodoItem] | None:
        """Tasks due within the look-ahead window, filtered for this list."""
        data = self.coordinator.data
        if data is None:
            return None
        lookahead = self.coordinator.config_entry.options.get(CONF_LOOKAHEAD_DAYS, DEFAULT_LOOKAHEAD_DAYS)
        language = self.hass.config.language if self.hass else "en"
        return [
            TodoItem(
                uid=task.id,
                summary=task.name,
                status=TodoItemStatus.NEEDS_ACTION,
                due=task.due,
                description=model.describe(task, data, language),
            )
            for task in model.tasks_due_by(data, today() + timedelta(days=lookahead))
            if self._include(task)
        ]


class TodyAllTasksList(TodyTodoList):
    """All due tasks."""

    _attr_translation_key = "all_tasks"

    def __init__(self, coordinator: TodyCoordinator) -> None:
        """Initialize the list."""
        super().__init__(coordinator, "all")

    def _include(self, task: Task) -> bool:
        return True


class _KeyedList(TodyTodoList):
    """A list bound to an area or participant that may disappear from Tody."""

    def __init__(self, coordinator: TodyCoordinator, kind: str, key: str) -> None:
        super().__init__(coordinator, f"{kind}_{key}")
        self._key = key

    def _item(self) -> model.Area | model.Participant | None:
        data = self.coordinator.data
        return None if data is None else self._mapping(data).get(self._key)

    def _mapping(self, data: model.TodyData) -> Mapping[str, model.Area | model.Participant]:
        raise NotImplementedError

    @property
    def available(self) -> bool:
        """Unavailable once the area/participant is removed in Tody."""
        return super().available and self._item() is not None

    @property
    def name(self) -> str | None:
        """Use the current Tody name."""
        item = self._item()
        return item.name if item else None


class TodyAreaList(_KeyedList):
    """Due tasks in one Tody area."""

    def __init__(self, coordinator: TodyCoordinator, area_id: str) -> None:
        """Initialize the list."""
        super().__init__(coordinator, "area", area_id)

    def _mapping(self, data: model.TodyData) -> Mapping[str, model.Area]:
        return data.areas

    def _include(self, task: Task) -> bool:
        return task.area_id == self._key


class TodyPersonList(_KeyedList):
    """Due tasks where it's this participant's turn."""

    def __init__(self, coordinator: TodyCoordinator, participant_id: str) -> None:
        """Initialize the list."""
        super().__init__(coordinator, "person", participant_id)

    def _mapping(self, data: model.TodyData) -> Mapping[str, model.Participant]:
        return data.participants

    def _include(self, task: Task) -> bool:
        return self._key in task.turn_participant_ids
