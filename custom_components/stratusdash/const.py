"""Constants for the StratusDash integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "stratusdash"
API_BASE_URL = "https://api.stratusdash.com/api/v1/home-assistant"
API_DEVICES_URL = f"{API_BASE_URL}/devices"
UPDATE_INTERVAL = timedelta(minutes=15)
PLATFORMS = (Platform.SENSOR, Platform.SWITCH, Platform.TIME, Platform.SELECT)
