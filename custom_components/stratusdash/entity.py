"""Shared StratusDash entity."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import StratusDashDevice
from .const import DOMAIN
from .coordinator import StratusDashCoordinator


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
