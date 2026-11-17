"""Config flow for StratusDash."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState, ConfigFlowResult
from homeassistant.const import CONF_TOKEN
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    StratusDashApiClient,
    StratusDashApiError,
    StratusDashAuthenticationError,
)
from .const import DOMAIN
from .display import DISPLAY_SENSORS, DOMAINS, selectable, sensor_ids, sensor_refs

TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="off")
        )
    }
)


class StratusDashConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a StratusDash account credential."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return DisplayOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token = str(user_input[CONF_TOKEN]).strip()
            account_id, error = await self._validate(token)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="StratusDash", data={CONF_TOKEN: token}
                )
        return self.async_show_form(
            step_id="user", data_schema=TOKEN_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Replace a revoked token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            token = str(user_input[CONF_TOKEN]).strip()
            account_id, error = await self._validate(token)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_TOKEN: token}
                )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=TOKEN_SCHEMA, errors=errors
        )

    async def _validate(self, token: str) -> tuple[str | None, str | None]:
        client = StratusDashApiClient(async_get_clientsession(self.hass), token)
        try:
            data = await client.async_get_devices()
        except StratusDashAuthenticationError:
            return None, "invalid_auth"
        except StratusDashApiError:
            return None, "cannot_connect"
        return data.account_id, None


class DisplayOptionsFlow(config_entries.OptionsFlow):
    """Choose sensors for one owned screen at a time."""

    async def async_step_init(self, user_input=None):
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_unavailable")
        coordinator = self.config_entry.runtime_data.coordinator
        if not hasattr(self, "devices"):
            try:
                data = await coordinator.client.async_get_devices()
            except StratusDashApiError:
                return self.async_abort(reason="entry_unavailable")
            if data.account_id != coordinator.account_id:
                return self.async_abort(reason="entry_unavailable")
            self.devices = {
                device.id: device for device in data.devices if device.display_setup
            }
        if not self.devices:
            return self.async_abort(reason="no_screens")
        if user_input and (device := self.devices.get(user_input["device"])):
            self.device = device
            self.original_refs = self.config_entry.options.get(DISPLAY_SENSORS, {}).get(
                device.id, []
            )
            return await self.async_step_sensors()
        options = [
            {"value": device.id, "label": device.name}
            for device in self.devices.values()
        ]
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )

    async def async_step_sensors(self, user_input=None):
        previous = [
            entity for entity in sensor_ids(self.hass, self.original_refs) if entity
        ]
        candidates = sorted(
            set(previous)
            | {
                state.entity_id
                for state in self.hass.states.async_all(DOMAINS)
                if selectable(state)
            }
        )
        if not candidates and not self.original_refs:
            return self.async_abort(reason="no_sensors")
        errors = {}
        if user_input is not None:
            chosen = user_input.get("sensors", [])
            if (
                len(chosen) > 4
                or len(set(chosen)) != len(chosen)
                or any(entity not in candidates for entity in chosen)
            ):
                errors["sensors"] = "invalid_sensors"
            else:
                switch = (
                    chosen
                    and user_input.get("weather_layout")
                    and self.device.display_setup != "readings"
                )
                try:
                    await self.config_entry.runtime_data.coordinator.async_save_sensors(
                        self.device.id,
                        sensor_refs(self.hass, chosen),
                        expected_refs=self.original_refs,
                        weather_layout_from=(self.device.layout_id or 0)
                        if switch
                        else None,
                    )
                except StratusDashApiError:
                    errors["base"] = "send_failed"
                else:
                    return self.async_create_entry(
                        title="", data=self.config_entry.options
                    )
        fields = {
            vol.Optional(
                "sensors",
                description={
                    "suggested_value": user_input.get("sensors", [])
                    if user_input is not None
                    else previous
                },
            ): EntitySelector(
                EntitySelectorConfig(
                    multiple=True,
                    include_entities=candidates,
                    filter={"domain": list(DOMAINS)},
                )
            )
        }
        if self.device.display_setup != "readings":
            fields[
                vol.Optional(
                    "weather_layout", default=self.device.display_setup == "weather"
                )
            ] = BooleanSelector()
        return self.async_show_form(
            step_id="sensors",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"device": self.device.name},
        )
