"""Overdue and due-today counters for Tody."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import model
from .coordinator import TodyConfigEntry, TodyCoordinator
from .entity import TodyEntity, today
from .model import Task, TodyData

PARALLEL_UPDATES = 0


def _overdue(data: TodyData, day: date) -> list[Task]:
    return model.tasks_due_by(data, day - timedelta(days=1))


def _due_today(data: TodyData, day: date) -> list[Task]:
    return [task for task in model.tasks_due_by(data, day) if task.due == day]


@dataclass(frozen=True, kw_only=True)
class TodySensorEntityDescription(SensorEntityDescription):
    """Describes a Tody task counter."""

    tasks_fn: Callable[[TodyData, date], list[Task]]


SENSORS: tuple[TodySensorEntityDescription, ...] = (
    TodySensorEntityDescription(
        key="overdue",
        translation_key="overdue",
        state_class=SensorStateClass.MEASUREMENT,
        tasks_fn=_overdue,
    ),
    TodySensorEntityDescription(
        key="due_today",
        translation_key="due_today",
        state_class=SensorStateClass.MEASUREMENT,
        tasks_fn=_due_today,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TodyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Tody sensors."""
    coordinator = entry.runtime_data
    async_add_entities(TodyTaskCountSensor(coordinator, description) for description in SENSORS)


class TodyTaskCountSensor(TodyEntity, SensorEntity):
    """Number of Tody tasks in a due state."""

    entity_description: TodySensorEntityDescription

    def __init__(self, coordinator: TodyCoordinator, description: TodySensorEntityDescription) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    def _tasks(self) -> list[Task]:
        data = self.coordinator.data
        return [] if data is None else self.entity_description.tasks_fn(data, today())

    @property
    def native_value(self) -> int:
        """Number of matching tasks."""
        return len(self._tasks())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Per-area counts and the task names."""
        tasks = self._tasks()
        areas = self.coordinator.data.areas if self.coordinator.data else {}
        per_area = Counter(
            areas[task.area_id].name if task.area_id in areas else "unknown" for task in tasks
        )
        return {"areas": dict(per_area), "tasks": [task.name for task in tasks]}
