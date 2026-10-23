"""Select an existing StratusDash layout."""

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from .entity import StratusDashEntity, async_discover


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_discover(
        entry,
        async_add_entities,
        lambda device_id: [
            LayoutSelect(entry.runtime_data.coordinator, device_id, "layout")
        ],
    )


class LayoutSelect(StratusDashEntity, SelectEntity):
    _attr_translation_key = "layout"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def options(self) -> list[str]:
        return [layout.name for layout in self.coordinator.data.layouts]

    @property
    def current_option(self) -> str | None:
        if not self.device:
            return None
        return next(
            (
                layout.name
                for layout in self.coordinator.data.layouts
                if layout.id == self.device.layout_id
            ),
            None,
        )

    async def async_select_option(self, option: str) -> None:
        layout = next(
            (item for item in self.coordinator.data.layouts if item.name == option),
            None,
        )
        if layout is None:
            raise HomeAssistantError("This layout is no longer available")
        await self.async_write(
            self.coordinator.client.async_set_layout(self._device_id, layout.id)
        )
