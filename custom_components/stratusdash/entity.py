"""Shared StratusDash entity and device discovery."""

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import StratusDashApiError, StratusDashAuthenticationError, StratusDashDevice
from .const import DOMAIN
from .coordinator import StratusDashCoordinator


def async_discover(entry, async_add_entities, factory) -> None:
    """Add each device's platform entities once, including later-owned devices."""
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def discover() -> None:
        for device in coordinator.data.devices:
            if device.id not in known:
                known.add(device.id)
                async_add_entities(factory(device.id))

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class StratusDashEntity(CoordinatorEntity[StratusDashCoordinator]):
    """An entity belonging to one StratusDash device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: StratusDashCoordinator, device_id: str, key: str
    ) -> None:
        super().__init__(coordinator, context=device_id)
        self._device_id = device_id
        self._attr_unique_id = f"{coordinator.account_id}_{device_id}_{key}"

    @property
    def device(self) -> StratusDashDevice | None:
        return self.coordinator.data.device(self._device_id)

    @property
    def device_info(self) -> DeviceInfo:
        device = self.device
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer="StratusDash",
            name=device.name if device else None,
        )

    @property
    def available(self) -> bool:
        return super().available and self.device is not None

    @property
    def extra_state_attributes(self) -> dict | None:
        if (device := self.device) is None:
            return None
        return {
            "last_sample_at": device.received_at,
            "quiet_until": device.quiet_until,
            "device_timezone": device.sleep_hours.timezone_id,
        }

    async def async_write(self, operation) -> None:
        try:
            await operation
        except StratusDashAuthenticationError as error:
            self.coordinator.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(str(error)) from error
        except StratusDashApiError as error:
            raise HomeAssistantError(str(error)) from error
        await self.coordinator.async_refresh()
