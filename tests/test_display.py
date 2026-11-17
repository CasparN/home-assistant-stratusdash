"""The display picker uses the paired account and saves per-device readings."""

from homeassistant import data_entry_flow
from homeassistant.core import CoreState
from homeassistant.helpers import entity_registry as er

from custom_components.stratusdash.const import API_DEVICES_URL
from custom_components.stratusdash.display import DISPLAY_SENSORS

from .conftest import DEVICE_ID, OTHER_ACCOUNT_ID, payload


async def open_picker(hass, entry):
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        flow["flow_id"], {"device": DEVICE_ID}
    )


async def test_select_send_refresh_and_clear(hass, config_entry, aioclient_mock):
    body = payload()
    body["devices"][0]["display_setup"] = "weather"
    aioclient_mock.get(API_DEVICES_URL, json=body)
    url = f"{API_DEVICES_URL}/{DEVICE_ID}/display"
    aioclient_mock.put(url, status=204)
    aioclient_mock.delete(url, status=204)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    hass.states.async_set(
        "sensor.room", "0", {"friendly_name": "Séjour", "unit_of_measurement": "°C"}
    )
    hass.states.async_set("sensor.out", "unavailable", {"unit_of_measurement": "W"})
    flow = await open_picker(hass, config_entry)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {"sensors": ["sensor.room", "sensor.out"], "weather_layout": True},
    )
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert config_entry.options[DISPLAY_SENSORS] == {
        DEVICE_ID: ["sensor.room", "sensor.out"]
    }
    writes = [call for call in aioclient_mock.mock_calls if call[0] == "PUT"]
    assert len(writes) == 1
    assert writes[0][1].query["weather_layout_from"] == "1"
    assert writes[0][2]["items"] == [
        {"name": "Sejour", "unit": "~C", "value": 0.0},
        {"name": "out", "unit": "W", "value": None},
    ]
    hass.state = CoreState.running
    hass.states.async_set("sensor.out", "2", {"unit_of_measurement": "W"})
    await config_entry.runtime_data.coordinator.async_refresh()
    writes = [call for call in aioclient_mock.mock_calls if call[0] == "PUT"]
    assert len(writes) == 2 and not writes[-1][1].query
    assert writes[-1][2]["items"][1]["value"] == 2
    flow = await open_picker(hass, config_entry)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {})
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert config_entry.options[DISPLAY_SENSORS] == {}
    assert aioclient_mock.mock_calls[-1][0] == "DELETE"
    await config_entry.runtime_data.coordinator.async_refresh()
    assert len([c for c in aioclient_mock.mock_calls if c[0] == "PUT"]) == 2


async def test_rejected_save_and_hidden_devices(hass, config_entry, aioclient_mock):
    body = payload()
    body["devices"][0]["display_setup"] = "custom"
    body["devices"].append(
        dict(body["devices"][0], id=OTHER_ACCOUNT_ID, display_setup=None)
    )
    aioclient_mock.get(API_DEVICES_URL, json=body)
    aioclient_mock.put(f"{API_DEVICES_URL}/{DEVICE_ID}/display", status=403)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    hass.states.async_set("sensor.energy", "50")
    flow = await hass.config_entries.options.async_init(config_entry.entry_id)
    selector = next(iter(flow["data_schema"].schema.values()))
    assert [o["value"] for o in selector.config["options"]] == [DEVICE_ID]
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"device": DEVICE_ID}
    )
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"sensors": ["sensor.energy"]}
    )
    assert "send_failed" in result["errors"].values()
    assert DISPLAY_SENSORS not in config_entry.options
    assert not aioclient_mock.mock_calls[-1][1].query


async def test_saved_entity_rename_order_and_limit(hass, config_entry, aioclient_mock):
    body = payload()
    body["devices"][0]["display_setup"] = "readings"
    aioclient_mock.get(API_DEVICES_URL, json=body)
    aioclient_mock.put(f"{API_DEVICES_URL}/{DEVICE_ID}/display", status=204)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "sensor", "test", "123", suggested_object_id="watts"
    )
    for index in range(5):
        hass.states.async_set(f"sensor.s{index}", "4")
    hass.states.async_set(entry.entity_id, "12")
    flow = await open_picker(hass, config_entry)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"sensors": [f"sensor.s{i}" for i in range(5)]}
    )
    assert flow["errors"] == {"sensors": "invalid_sensors"}
    await hass.config_entries.options.async_configure(
        flow["flow_id"], {"sensors": [entry.entity_id]}
    )
    assert config_entry.options[DISPLAY_SENSORS][DEVICE_ID] == [entry.id]
    registry.async_update_entity(entry.entity_id, new_entity_id="sensor.power_meter")
    hass.states.async_set("sensor.power_meter", "29")
    await config_entry.runtime_data.coordinator._refresh_displays(
        config_entry.runtime_data.coordinator.data
    )
    assert aioclient_mock.mock_calls[-1][2]["items"][0]["value"] == 29


async def test_ineligible_or_old_server(hass, config_entry, aioclient_mock):
    aioclient_mock.get(API_DEVICES_URL, json=payload())
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    flow = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert flow["reason"] == "no_screens"


async def test_clear_stops_sharing_during_outage(hass, config_entry, aioclient_mock):
    from unittest.mock import AsyncMock, patch

    from custom_components.stratusdash.api import StratusDashConnectionError

    body = payload()
    body["devices"][0]["display_setup"] = "readings"
    aioclient_mock.get(API_DEVICES_URL, json=body)
    aioclient_mock.put(f"{API_DEVICES_URL}/{DEVICE_ID}/display", status=204)
    hass.config_entries.async_update_entry(
        config_entry, options={DISPLAY_SENSORS: {DEVICE_ID: ["sensor.energy"]}}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    coordinator = config_entry.runtime_data.coordinator
    with patch.object(
        coordinator.client, "async_set_display", new_callable=AsyncMock
    ) as push:
        push.side_effect = StratusDashConnectionError()
        await coordinator.async_save_sensors(
            DEVICE_ID, [], expected_refs=["sensor.energy"], weather_layout_from=None
        )
        assert config_entry.options[DISPLAY_SENSORS] == {DEVICE_ID: []}
        push.side_effect = None
        await coordinator._refresh_displays(coordinator.data)
        push.assert_awaited_with(DEVICE_ID, [])
        assert config_entry.options[DISPLAY_SENSORS] == {}
