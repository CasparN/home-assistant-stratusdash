"""Constants for the StratusDash integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "stratusdash"
API_DEVICES_URL = "https://api.stratusdash.com/api/v1/home-assistant/devices"
UPDATE_INTERVAL = timedelta(minutes=15)
PLATFORMS = (Platform.SENSOR,)
