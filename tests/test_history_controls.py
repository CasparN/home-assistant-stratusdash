"""Recorder recovery and owner settings through real HA entities."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    get_metadata,
    statistics_during_period,
)
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.stratusdash.api import (
    StratusDashApiClient,
    StratusDashConnectionError,
    StratusDashInvalidResponseError,
    _parse_data,
)
from custom_components.stratusdash.const import API_BASE_URL, API_DEVICES_URL, DOMAIN
from custom_components.stratusdash.statistics import async_import_history, statistic_id

from .conftest import ACCOUNT_ID, DEVICE_ID, OTHER_ACCOUNT_ID, TOKEN, payload

# Preserve the real HTTP boundary before the autouse history mock is installed.
_REAL_GET_HISTORY = StratusDashApiClient.async_get_history


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


# Extended by Step 7 coverage audit, including the final generation pass.
# Value: protects=complete ordered series and persisted units, means and no sums;
# fails_when=unordered hours are lost or recorder metadata mislabels measurements;
# why_new=prior coverage has one valid hour per series and no metadata assertions;
# seam=none; no_claim=backend aggregation (mocked history, real recorder)
async def test_late_history_updates_once_without_live_events_and_survives_reload(
    hass, recorder_mock, aioclient_mock, config_entry, mock_history
):
    end = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    hour = end - timedelta(hours=4)
    later = hour + timedelta(hours=2)
    rows = [hour_row(later, mean=23), hour_row(hour)]
    missing = hour_row(hour + timedelta(hours=1))
    for field in (
        "temperature_c",
        "humidity_percent",
        "pressure_hpa",
        "battery_voltage_v",
    ):
        missing[field] = None
    unowned = hour_row(hour)
    unowned["device_id"] = OTHER_ACCOUNT_ID
    rows.extend([missing, unowned])
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
    rows[1] = hour_row(hour, mean=22)
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
    assert all(
        [row["start"] for row in values] == [hour.timestamp(), later.timestamp()]
        for values in stats.values()
    )
    temperature = stats[statistic_id(ACCOUNT_ID, DEVICE_ID, "temperature")][0]
    assert (temperature["min"], temperature["max"], temperature["mean"]) == (18, 24, 22)
    assert temperature["start"] == hour.timestamp()
    prefix = (
        "stratusdash:028db9539ddb49b69e86b2459e31e659_c76f5d751a364df3ac4286c64b2b6284_"
    )
    for key, expected in {
        "temperature": (18, 24, 22),
        "humidity": (40, 50, 45),
        "pressure": (1000, 1010, 1005),
        "battery_voltage": (3.8, 3.9, 3.85),
    }.items():
        row = stats[prefix + key][0]
        assert (row["min"], row["max"], row["mean"]) == expected
        later_row = stats[prefix + key][1]
        later_expected = (18, 24, 23) if key == "temperature" else expected
        assert (
            later_row["min"],
            later_row["max"],
            later_row["mean"],
        ) == later_expected

    metadata = await recorder_mock.async_add_executor_job(get_metadata, hass)
    for key, unit, unit_class in (
        ("temperature", "°C", "temperature"),
        ("humidity", "%", None),
        ("pressure", "hPa", "pressure"),
        ("battery_voltage", "V", "voltage"),
    ):
        stored = metadata[prefix + key][1]
        assert stored["unit_of_measurement"] == unit
        assert stored["unit_class"] == unit_class
        assert stored["mean_type"] == StatisticMeanType.ARITHMETIC
        assert stored["has_sum"] is False

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await async_wait_recording_done(hass)
    assert mock_history.call_args.args[0] == end - timedelta(days=7)
    assert await read_stats() == stats
    assert await recorder_mock.async_add_executor_job(get_metadata, hass) == metadata


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


async def test_control_refresh_survives_a_slow_history_poll(
    hass, aioclient_mock, config_entry, mock_history
):
    """A completed write must refresh even after the debounce timer expires."""
    aioclient_mock.get(API_DEVICES_URL, json=payload())
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    coordinator = config_entry.runtime_data.coordinator
    entity_id = er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, f"{ACCOUNT_ID}_{DEVICE_ID}_quiet_hours"
    )
    entered_history, release_history, written = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )

    async def slow_history(start):
        entered_history.set()
        await release_history.wait()
        return history(start, [])

    mock_history.side_effect = slow_history
    poll = hass.async_create_background_task(coordinator.async_refresh(), "slow poll")
    await asyncio.wait_for(entered_history.wait(), 2)
    updated = payload()
    updated["devices"][0]["sleep_hours"]["enabled"] = True
    aioclient_mock.clear_requests()
    aioclient_mock.get(API_DEVICES_URL, json=updated)
    aioclient_mock.patch(f"{API_BASE_URL}/devices/{DEVICE_ID}/sleep-hours", status=204)
    original_write = coordinator.client.async_set_sleep_hours

    async def write(*args, **kwargs):
        await original_write(*args, **kwargs)
        written.set()

    with patch.object(coordinator.client, "async_set_sleep_hours", side_effect=write):
        control = hass.async_create_background_task(
            hass.services.async_call(
                "switch", "turn_on", {"entity_id": entity_id}, blocking=True
            ),
            "control during poll",
        )
        try:
            await asyncio.wait_for(written.wait(), 2)
            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
            await asyncio.sleep(0)
        finally:
            release_history.set()
            await asyncio.wait_for(asyncio.gather(poll, control), 5)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "on"


# Extended by Step 7 coverage audit, final generation pass.
# Value: protects=invalid history envelopes and windows never queue partial imports;
# fails_when=window guards are removed or valid rows write before validation ends;
# why_new=existing cases cover row numbers and duplicates, not envelopes or windows;
# seam=none; no_claim=database rollback (recorder submission is mocked)
@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body["hours"][-1].update(start="2026-01-01T00:15:00Z"),
        lambda body: body["hours"].append(deepcopy(body["hours"][0])),
        lambda body: body["hours"][-1]["temperature_c"].update(mean=float("nan")),
        lambda body: body["hours"][-1]["temperature_c"].update(mean=100),
        lambda body: body["hours"][-1]["temperature_c"].update(mean=10**400),
        pytest.param(
            lambda body: body.update(start=body["hours"][0]["start"]),
            id="mismatched-start",
        ),
        pytest.param(lambda body: body.update(end=None), id="null-end"),
        pytest.param(
            lambda body: body.update(
                end=(
                    dt_util.parse_datetime(body["end"]) + timedelta(days=1)
                ).isoformat()
            ),
            id="future-end",
        ),
        pytest.param(
            lambda body: body.update(
                end=(
                    dt_util.parse_datetime(body["end"]) - timedelta(minutes=1)
                ).isoformat()
            ),
            id="nonhour-end",
        ),
        pytest.param(lambda body: body.update(end=body["start"]), id="empty-window"),
        pytest.param(lambda body: body.update(hours=None), id="null-hours"),
        pytest.param(lambda body: body.update(hours={}), id="object-hours"),
        pytest.param(lambda body: body.pop("hours"), id="missing-hours"),
        pytest.param(
            lambda body: body["hours"][-1].update(
                start=(
                    dt_util.parse_datetime(body["start"]) - timedelta(hours=1)
                ).isoformat()
            ),
            id="row-before-window",
        ),
        pytest.param(
            lambda body: body["hours"][-1].update(start=body["end"]),
            id="row-at-exclusive-end",
        ),
    ],
)
async def test_invalid_history_never_partially_imports(hass, mutate):
    end = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=1)
    body = history(
        start,
        [hour_row(end - timedelta(hours=2)), hour_row(end - timedelta(hours=1))],
    )
    mutate(body)
    with patch(
        "custom_components.stratusdash.statistics.async_add_external_statistics"
    ) as write:
        with pytest.raises(StratusDashInvalidResponseError):
            async_import_history(hass, _parse_data(payload()), body, start)
        write.assert_not_called()


# Extended by Step 7 coverage audit.
# Value: protects=minimal off/end writes and rejection of invalid control values;
# fails_when=off enables sleep, end changes start, or invalid controls reach the API;
# why_new=existing checks cover on, start, valid layout and backend rejection;
# seam=none; no_claim=hardware schedule application (HTTP responses are mocked)
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
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": entity("switch", "quiet_hours")},
        blocking=True,
    )
    await hass.services.async_call(
        "time",
        "set_value",
        {"entity_id": entity("time", "quiet_hours_end"), "time": "07:30:00"},
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
        ("PATCH", {"enabled": False}),
        ("PATCH", {"end_minute": 450}),
    ]

    calls_before = aioclient_mock.call_count
    with pytest.raises(HomeAssistantError, match="whole minutes"):
        await hass.services.async_call(
            "time",
            "set_value",
            {"entity_id": entity("time", "quiet_hours_start"), "time": "23:30:01"},
            blocking=True,
        )
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": entity("select", "layout"), "option": "Deleted layout"},
            blocking=True,
        )
    assert aioclient_mock.call_count == calls_before

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


# Added by Step 7 coverage audit.
# Value: protects=history query, bearer and reauth on history authentication errors;
# fails_when=history uses the wrong query or credential, or its auth error is swallowed;
# why_new=the autouse history mock bypasses the real API method in every existing test;
# seam=none; no_claim=live backend acceptance (mocked HTTP, real HA flow)
@pytest.mark.parametrize("failure", ["revoked", "wrong_account"])
async def test_history_http_contract_and_auth_failure(
    hass, aioclient_mock, config_entry, failure
):
    end = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)
    aioclient_mock.get(API_DEVICES_URL, json=payload())
    aioclient_mock.get(
        f"{API_BASE_URL}/history",
        params={"start": start.isoformat()},
        json=history(start, []),
    )
    with patch.object(StratusDashApiClient, "async_get_history", _REAL_GET_HISTORY):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        history_call = next(
            call
            for call in aioclient_mock.mock_calls
            if call[1].path.endswith("/history")
        )
        assert history_call[0] == "GET"
        assert history_call[1].query["start"] == start.isoformat()
        assert history_call[3]["Authorization"] == f"Bearer {TOKEN}"

        aioclient_mock.clear_requests()
        aioclient_mock.get(API_DEVICES_URL, json=payload())
        start = end - timedelta(days=1)
        bad_history = history(start, [])
        bad_history["account_id"] = OTHER_ACCOUNT_ID
        aioclient_mock.get(
            f"{API_BASE_URL}/history",
            params={"start": start.isoformat()},
            status=401 if failure == "revoked" else 200,
            json=bad_history,
        )
        coordinator = config_entry.runtime_data.coordinator
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert not coordinator.last_update_success
        assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        entity_id = er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{ACCOUNT_ID}_{DEVICE_ID}_temperature"
        )
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
