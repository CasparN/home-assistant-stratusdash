"""Recorder recovery and owner settings through real HA entities."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.stratusdash.api import (
    StratusDashConnectionError,
    StratusDashInvalidResponseError,
    _parse_data,
)
from custom_components.stratusdash.const import API_BASE_URL, API_DEVICES_URL, DOMAIN
from custom_components.stratusdash.statistics import async_import_history, statistic_id

from .conftest import ACCOUNT_ID, DEVICE_ID, payload


def history(start, rows):
    return {
        "account_id": ACCOUNT_ID,
        "start": start.isoformat(),
        "end": dt_util.utcnow().replace(minute=0, second=0, microsecond=0).isoformat(),
        "hours": rows,
    }


def hour_row(hour, mean=20):
    return {
        "device_id": DEVICE_ID,
        "start": hour.isoformat(),
        "temperature_c": {"min": 18, "max": 24, "mean": mean},
        "humidity_percent": {"min": 40, "max": 50, "mean": 45},
        "pressure_hpa": {"min": 1000, "max": 1010, "mean": 1005},
        "battery_voltage_v": {"min": 3.8, "max": 3.9, "mean": 3.85},
    }


async def test_late_history_updates_once_without_live_events_and_survives_reload(
    hass, recorder_mock, aioclient_mock, config_entry, mock_history
):
    end = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    hour = end - timedelta(hours=4)
    rows = [hour_row(hour)]
    mock_history.side_effect = lambda start: history(start, rows)
    aioclient_mock.get(API_DEVICES_URL, json=payload())
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await async_wait_recording_done(hass)
    coordinator = config_entry.runtime_data.coordinator
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{ACCOUNT_ID}_{DEVICE_ID}_temperature"
    )
    assert mock_history.call_args.args[0] == end - timedelta(days=7)
    assert hass.states.get(entity_id).state == "21.5"
    assert hass.states.get(entity_id).attributes[
        "history_statistic_id"
    ] == statistic_id(ACCOUNT_ID, DEVICE_ID, "temperature")
    events = []

    @callback
    def on_change(event):
        if event.data["entity_id"] == entity_id:
            events.append(event)

    unsubscribe = hass.bus.async_listen("state_changed", on_change)
    rows[0] = hour_row(hour, mean=22)
    await coordinator.async_refresh()
    await async_wait_recording_done(hass)
    assert mock_history.call_args.args[0] == end - timedelta(days=1)
    assert not events
    assert hass.states.get(entity_id).state == "21.5"
    unsubscribe()

    async def read_stats():
        return await recorder_mock.async_add_executor_job(
            statistics_during_period,
            hass,
            hour,
            end,
            None,
            "hour",
            None,
            {"min", "max", "mean"},
        )

    stats = await read_stats()
    assert len(stats) == 4
    assert all(len(values) == 1 for values in stats.values())
    temperature = stats[statistic_id(ACCOUNT_ID, DEVICE_ID, "temperature")][0]
    assert (temperature["min"], temperature["max"], temperature["mean"]) == (18, 24, 22)
    assert temperature["start"] == hour.timestamp()

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await async_wait_recording_done(hass)
    assert mock_history.call_args.args[0] == end - timedelta(days=7)
    assert await read_stats() == stats


async def test_history_failure_retries_without_disabling_current_values(
    hass, aioclient_mock, config_entry, mock_history
):
    aioclient_mock.get(API_DEVICES_URL, json=payload())
    mock_history.side_effect = StratusDashConnectionError("Unavailable")
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data.coordinator
    assert coordinator.last_update_success
    assert coordinator._history_end is None
    mock_history.side_effect = lambda start: history(start, [])
    await coordinator.async_refresh()
    assert coordinator._history_end is not None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body["hours"][0].update(start="2026-01-01T00:15:00Z"),
        lambda body: body["hours"].append(deepcopy(body["hours"][0])),
        lambda body: body["hours"][0]["temperature_c"].update(mean=float("nan")),
        lambda body: body["hours"][0]["temperature_c"].update(mean=100),
        lambda body: body["hours"][0]["temperature_c"].update(mean=10**400),
    ],
)
async def test_invalid_history_never_partially_imports(hass, mutate):
    import json

    end = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=1)
    body = history(start, [hour_row(end - timedelta(hours=1))])
    mutate(body)
    with patch(
        "custom_components.stratusdash.statistics.async_add_external_statistics"
    ) as write:
        with pytest.raises(StratusDashInvalidResponseError):
            async_import_history(
                hass, _parse_data(json.dumps(payload()).encode()), body, start
            )
        write.assert_not_called()


async def test_controls_work_while_sleeping_and_send_only_changed_setting(
    hass, aioclient_mock, config_entry
):
    snapshot = payload()
    device = snapshot["devices"][0]
    device["available"] = False
    device["quiet_until"] = (dt_util.utcnow() + timedelta(hours=2)).isoformat()
    aioclient_mock.get(API_DEVICES_URL, json=snapshot)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)

    def entity(domain, key):
        return registry.async_get_entity_id(
            domain, DOMAIN, f"{ACCOUNT_ID}_{DEVICE_ID}_{key}"
        )

    assert hass.states.get(entity("sensor", "temperature")).state == STATE_UNAVAILABLE
    assert hass.states.get(entity("switch", "quiet_hours")).state == "off"
    assert hass.states.get(entity("time", "quiet_hours_start")).state == "01:00:00"
    assert (
        hass.states.get(entity("time", "quiet_hours_start")).attributes[
            "device_timezone"
        ]
        == "Europe/Amsterdam"
    )
    assert hass.states.get(entity("select", "layout")).attributes["options"] == [
        "Complete",
        "Compact",
    ]
    base = f"{API_BASE_URL}/devices/{DEVICE_ID}"
    aioclient_mock.patch(base + "/sleep-hours", status=204)
    aioclient_mock.put(base + "/layout", status=204)
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": entity("switch", "quiet_hours")},
        blocking=True,
    )
    await hass.services.async_call(
        "time",
        "set_value",
        {"entity_id": entity("time", "quiet_hours_start"), "time": "23:30:00"},
        blocking=True,
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity("select", "layout"), "option": "Compact"},
        blocking=True,
    )
    writes = [
        (call[0], call[2])
        for call in aioclient_mock.mock_calls
        if call[0] in ("PATCH", "PUT")
    ]
    assert writes == [
        ("PATCH", {"enabled": True}),
        ("PATCH", {"start_minute": 1410}),
        ("PUT", {"layout_id": 2}),
    ]

    aioclient_mock.clear_requests()
    aioclient_mock.patch(base + "/sleep-hours", status=422)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "time",
            "set_value",
            {"entity_id": entity("time", "quiet_hours_end"), "time": "23:30:00"},
            blocking=True,
        )
    assert hass.states.get(entity("time", "quiet_hours_end")).state == "07:00:00"

    aioclient_mock.clear_requests()
    aioclient_mock.patch(base + "/sleep-hours", status=401)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": entity("switch", "quiet_hours")},
            blocking=True,
        )
    await hass.async_block_till_done()
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)
