"""Shared fixtures for the StratusDash custom integration."""

from homeassistant.const import CONF_TOKEN
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stratusdash.const import DOMAIN

ACCOUNT_ID = "028db953-9ddb-49b6-9e86-b2459e31e659"
OTHER_ACCOUNT_ID = "d53739a1-23e8-45b0-8d3f-b2cc6bdbcb3e"
DEVICE_ID = "c76f5d75-1a36-4df3-ac42-86c64b2b6284"
TOKEN = f"sdha_{'A' * 43}"
NEW_TOKEN = f"sdha_{'B' * 43}"


def payload(
    *,
    account_id: str = ACCOUNT_ID,
    devices: list[dict] | None = None,
) -> dict:
    """Return a representative backend snapshot."""
    if devices is None:
        devices = [
            {
                "id": DEVICE_ID,
                "name": "Living room",
                "available": True,
                "received_at": "2026-07-24T08:00:00Z",
                "temperature_c": 21.5,
                "humidity_percent": 48.0,
                "pressure_hpa": 1013.2,
                "battery_voltage_v": 3.82,
            }
        ]
    return {"account_id": account_id, "devices": devices}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow Home Assistant to load the repository custom integration."""
    yield


@pytest.fixture(name="config_entry")
def config_entry_fixture(hass) -> MockConfigEntry:
    """Create the canonical account config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="StratusDash",
        data={CONF_TOKEN: TOKEN},
        unique_id=ACCOUNT_ID,
        version=1,
    )
    entry.add_to_hass(hass)
    return entry
