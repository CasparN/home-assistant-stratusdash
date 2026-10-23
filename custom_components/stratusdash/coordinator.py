"""Data coordinator for StratusDash."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    StratusDashApiClient,
    StratusDashApiError,
    StratusDashAuthenticationError,
    StratusDashData,
)
from .const import DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class StratusDashCoordinator(DataUpdateCoordinator[StratusDashData]):
    """Poll one StratusDash account."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: StratusDashApiClient,
    ) -> None:
        if entry.unique_id is None:
            raise ValueError("StratusDash entries require an account ID")
        self.account_id = entry.unique_id
        self.client = client
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            always_update=False,
        )

    async def _async_update_data(self) -> StratusDashData:
        try:
            data = await self.client.async_get_devices()
        except StratusDashAuthenticationError as error:
            raise ConfigEntryAuthFailed("Invalid StratusDash credential") from error
        except StratusDashApiError as error:
            raise UpdateFailed("Could not update StratusDash") from error
        if data.account_id != self.account_id:
            raise ConfigEntryAuthFailed(
                "The credential belongs to a different StratusDash account"
            )
        return data
