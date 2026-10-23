"""Quiet-hours times in the device's configured timezone."""

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from .entity import StratusDashEntity, async_discover


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_discover(
        entry,
        async_add_entities,
        lambda device_id: (
            QuietHoursTime(entry.runtime_data.coordinator, device_id, boundary)
            for boundary in ("start", "end")
        ),
    )


class QuietHoursTime(StratusDashEntity, TimeEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, device_id, boundary) -> None:
        super().__init__(coordinator, device_id, f"quiet_hours_{boundary}")
        self._attr_translation_key = f"quiet_hours_{boundary}"
        self._field = f"{boundary}_minute"

    @property
    def native_value(self) -> time | None:
        if not self.device:
            return None
        minute = getattr(self.device.sleep_hours, self._field)
        return time(minute // 60, minute % 60)

    async def async_set_value(self, value: time) -> None:
        if value.second or value.microsecond:
            raise HomeAssistantError("Quiet hours use whole minutes")
        await self.async_write(
            self.coordinator.client.async_set_sleep_hours(
                self._device_id, **{self._field: value.hour * 60 + value.minute}
            )
        )
