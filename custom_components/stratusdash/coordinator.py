"""Data coordinator for StratusDash."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    StratusDashApiClient,
    StratusDashApiError,
    StratusDashAuthenticationError,
    StratusDashData,
)
from .const import DOMAIN, UPDATE_INTERVAL
from .statistics import async_import_history

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
        self._history_end = None
        self._history_failed = False
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
        end = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        # Reimport the previous night: buffered batches can arrive out of order.
        start = (
            end - timedelta(days=7)
            if self._history_end is None
            else max(end - timedelta(days=7), self._history_end - timedelta(days=1))
        )
        try:
            history = await self.client.async_get_history(start)
            self._history_end = async_import_history(self.hass, data, history, start)
            self._history_failed = False
        except StratusDashAuthenticationError as error:
            raise ConfigEntryAuthFailed("Invalid StratusDash credential") from error
        except StratusDashApiError, HomeAssistantError:
            if not self._history_failed:
                _LOGGER.warning(
                    "Could not import StratusDash history; will retry on the next poll"
                )
            self._history_failed = True
        return data
