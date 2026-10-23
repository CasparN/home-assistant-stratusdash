"""Quiet-hours switch."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory

from .entity import StratusDashEntity, async_discover


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_discover(
        entry,
        async_add_entities,
        lambda device_id: [
            QuietHoursSwitch(entry.runtime_data.coordinator, device_id, "quiet_hours")
        ],
    )


class QuietHoursSwitch(StratusDashEntity, SwitchEntity):
    _attr_translation_key = "quiet_hours"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool | None:
        return self.device.sleep_hours.enabled if self.device else None

    async def async_turn_on(self, **kwargs) -> None:
        await self.async_write(
            self.coordinator.client.async_set_sleep_hours(self._device_id, enabled=True)
        )

    async def async_turn_off(self, **kwargs) -> None:
        await self.async_write(
            self.coordinator.client.async_set_sleep_hours(
                self._device_id, enabled=False
            )
        )
