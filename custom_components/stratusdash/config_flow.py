"""Config flow for StratusDash."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
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

TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="off")
        )
    }
)


class StratusDashConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a read-only StratusDash account credential."""

    VERSION = 1

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
