"""Data coordinator for StratusDash."""

from __future__ import annotations

import asyncio
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
from .display import DISPLAY_SENSORS, display_items
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
        self._display_lock = asyncio.Lock()
        self._stop_display_listener = None
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

    def update_display_polling(self) -> None:
        """Keep polling even if every StratusDash entity is disabled in HA."""
        if self.config_entry.options.get(DISPLAY_SENSORS):
            if self._stop_display_listener is None:
                self._stop_display_listener = self.async_add_listener(lambda: None)
        elif self._stop_display_listener is not None:
            self.stop_display_polling()

    def stop_display_polling(self) -> None:
        if self._stop_display_listener is not None:
            self._stop_display_listener()
            self._stop_display_listener = None

    async def async_save_sensors(
        self, device_id, refs, *, expected_refs, weather_layout_from
    ):
        async with self._display_lock:
            choices = dict(self.config_entry.options.get(DISPLAY_SENSORS, {}))
            if choices.get(device_id, []) != expected_refs:
                raise StratusDashApiError("Device selections have changed")
            if refs:
                await self.client.async_set_display(
                    device_id,
                    display_items(self.hass, refs),
                    weather_layout_from=weather_layout_from,
                )
                choices[device_id] = refs
            elif device_id in choices:
                # An empty list stops publishing immediately and survives restart
                # as a pending cloud clear if the network goes down.
                choices[device_id] = []
                self._store_selections(choices)
                try:
                    await self.client.async_set_display(device_id, [])
                except StratusDashApiError:
                    return
                del choices[device_id]
            self._store_selections(choices)

    def _store_selections(self, choices: dict) -> None:
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={**self.config_entry.options, DISPLAY_SENSORS: dict(choices)},
        )
        self.update_display_polling()

    async def _refresh_displays(self, data: StratusDashData) -> None:
        async with self._display_lock:
            saved = self.config_entry.options.get(DISPLAY_SENSORS, {})
            choices = dict(saved)
            for device_id, refs in saved.items():
                device = data.device(device_id)
                if device is None:
                    del choices[device_id]
                    continue
                if not device.display_setup:
                    continue
                try:
                    await self.client.async_set_display(
                        device_id, display_items(self.hass, refs)
                    )
                except StratusDashAuthenticationError as error:
                    raise ConfigEntryAuthFailed(
                        "Invalid StratusDash credential"
                    ) from error
                except StratusDashApiError:
                    _LOGGER.warning(
                        "Could not update screen readings; retrying on next poll"
                    )
                else:
                    if not refs:
                        del choices[device_id]
            if choices != saved:
                self._store_selections(choices)

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
        if self.hass.is_running and self.config_entry.options.get(DISPLAY_SENSORS):
            await self._refresh_displays(data)
        return data
