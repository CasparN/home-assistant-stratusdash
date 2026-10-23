"""Sensor entities for StratusDash."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfPressure,
    UnitOfRatio,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import StratusDashConfigEntry
from .api import StratusDashDevice
from .entity import StratusDashEntity


@dataclass(frozen=True, kw_only=True)
class Description(SensorEntityDescription):
    value_fn: Callable[[StratusDashDevice], StateType]


DESCRIPTIONS = (
    Description(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda device: device.temperature_c,
    ),
    Description(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=UnitOfRatio.PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda device: device.humidity_percent,
    ),
    Description(
        key="pressure",
        translation_key="pressure",
        device_class=SensorDeviceClass.ATMOSPHERIC_PRESSURE,
        native_unit_of_measurement=UnitOfPressure.HPA,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda device: device.pressure_hpa,
    ),
    Description(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.battery_voltage_v,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StratusDashConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors and discover devices added after setup."""
    coordinator = entry.runtime_data.coordinator
    known: set[tuple[str, str]] = set()

    @callback
    def add_new_entities() -> None:
        current = {
            (device.id, description.key)
            for device in coordinator.data.devices
            for description in DESCRIPTIONS
        }
        new = current - known
        known.update(new)
        by_key = {description.key: description for description in DESCRIPTIONS}
        async_add_entities(
            StratusDashSensor(coordinator, device_id, by_key[key])
            for device_id, key in sorted(new)
        )

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))


class StratusDashSensor(StratusDashEntity, SensorEntity):
    entity_description: Description

    def __init__(self, coordinator, device_id: str, description: Description) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        device = self.device
        return None if device is None else self.entity_description.value_fn(device)

    @property
    def available(self) -> bool:
        device = self.device
        return super().available and device is not None and device.available
