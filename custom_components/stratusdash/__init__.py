"""The StratusDash Home Assistant integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import StratusDashApiClient
from .const import PLATFORMS
from .coordinator import StratusDashCoordinator


@dataclass(slots=True)
class StratusDashRuntimeData:
    coordinator: StratusDashCoordinator


type StratusDashConfigEntry = ConfigEntry[StratusDashRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: StratusDashConfigEntry) -> bool:
    """Set up StratusDash from a config entry."""
    token = entry.data.get(CONF_TOKEN)
    if not isinstance(token, str) or not token:
        raise ConfigEntryAuthFailed("The StratusDash credential is missing")
    client = StratusDashApiClient(async_get_clientsession(hass), token)
    coordinator = StratusDashCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = StratusDashRuntimeData(coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: StratusDashConfigEntry
) -> bool:
    """Unload a StratusDash config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
