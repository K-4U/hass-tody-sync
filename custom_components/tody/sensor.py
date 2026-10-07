"""Overdue and due-today counters for Tody."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import model
from .areas import ha_area_of
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

    async def async_added_to_hass(self) -> None:
        """Also refresh the ha_area attributes when a list's or device's area changes."""
        await super().async_added_to_hass()

        @callback
        def _area_changed(event_data: er.EventEntityRegistryUpdatedData) -> bool:
            return event_data["action"] == "update" and "area_id" in event_data["changes"]

        @callback
        def _device_area_changed(event_data: dr.EventDeviceRegistryUpdatedData) -> bool:
            # New area devices are created straight into their suggested area.
            return event_data["action"] == "create" or (
                event_data["action"] == "update" and "area_id" in event_data["changes"]
            )

        self.async_on_remove(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._async_area_changed, event_filter=_area_changed
            )
        )
        self.async_on_remove(
            self.hass.bus.async_listen(
                dr.EVENT_DEVICE_REGISTRY_UPDATED, self._async_area_changed, event_filter=_device_area_changed
            )
        )

    @callback
    def _async_area_changed(self, _event: Event) -> None:
        self.async_write_ha_state()

    def _tasks(self) -> list[Task]:
        data = self.coordinator.data
        return [] if data is None else self.entity_description.tasks_fn(data, today())

    @property
    def native_value(self) -> int:
        """Number of matching tasks."""
        return len(self._tasks())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Per-area counts, task names, and per task its Tody and linked HA area."""
        tasks = self._tasks()
        areas = self.coordinator.data.areas if self.coordinator.data else {}
        masterdata_id = self.coordinator.masterdata_id
        per_area = Counter(
            areas[task.area_id].name if task.area_id in areas else "unknown" for task in tasks
        )
        items = [
            {
                "name": task.name,
                "area": areas[task.area_id].name if task.area_id in areas else None,
                "ha_area": ha_area_of(self.hass, self.coordinator.config_entry.entry_id, masterdata_id, task.area_id) if task.area_id else None,
                "due": task.due.isoformat() if task.due else None,
            }
            for task in tasks
        ]
        return {
            "areas": dict(per_area),
            "tasks": [task.name for task in tasks],
            "ha_areas": sorted({item["ha_area"] for item in items if item["ha_area"]}),
            "items": items,
        }
