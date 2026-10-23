"""Focused behavior tests for the minimal StratusDash integration."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from unittest.mock import Mock, PropertyMock, patch

from aiohttp import ClientConnectionError, StreamReader
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.const import CONF_TOKEN, STATE_UNAVAILABLE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

from custom_components.stratusdash.api import (
    StratusDashApiClient,
    StratusDashAuthenticationError,
    StratusDashConnectionError,
    StratusDashInvalidResponseError,
)
from custom_components.stratusdash.const import API_DEVICES_URL, DOMAIN

from .conftest import (
    ACCOUNT_ID,
    DEVICE_ID,
    NEW_TOKEN,
    OTHER_ACCOUNT_ID,
    TOKEN,
    payload,
)


async def test_api_parses_bounds_and_maps_auth(hass, aioclient_mock) -> None:
    """The client parses snapshots and keeps auth errors safe."""
    aioclient_mock.get(API_DEVICES_URL, json=payload())
    client = StratusDashApiClient(async_get_clientsession(hass), TOKEN)
    data = await client.async_get_devices()
    assert data.account_id == ACCOUNT_ID
    assert data.devices[0].temperature_c == 21.5
    headers = aioclient_mock.mock_calls[0][3]
    assert headers["Authorization"] == f"Bearer {TOKEN}"

    aioclient_mock.clear_requests()
    aioclient_mock.get(API_DEVICES_URL, status=401, text=TOKEN)
    with pytest.raises(StratusDashAuthenticationError) as error:
        await client.async_get_devices()
    assert TOKEN not in str(error.value)


# Added by Step 7 coverage audit, final generation pass.
# Value: protects=safe transport errors and requests that forbid redirects;
# fails_when=transport exceptions escape or redirect following is enabled;
# why_new=HTTP status tests do not raise network errors or check redirect policy;
# seam=none;
# no_claim=live redirects or deadlines (mocked HTTP, request policy asserted)
@pytest.mark.parametrize("failure", ["timeout", "connection", "redirect"])
async def test_api_transport_failures_do_not_follow_redirects(
    hass, aioclient_mock, failure
):
    if failure == "redirect":
        aioclient_mock.get(
            API_DEVICES_URL,
            status=302,
            headers={"Location": API_DEVICES_URL + "/redirected"},
            text=TOKEN,
        )
        aioclient_mock.get(API_DEVICES_URL + "/redirected", json=payload())
    else:
        exception = TimeoutError if failure == "timeout" else ClientConnectionError
        aioclient_mock.get(API_DEVICES_URL, exc=exception(TOKEN))

    session = async_get_clientsession(hass)
    client = StratusDashApiClient(session, TOKEN)
    with patch.object(session, "request", wraps=session.request) as request:
        with pytest.raises(StratusDashConnectionError) as error:
            await client.async_get_devices()
        assert request.call_args.kwargs["allow_redirects"] is False
    assert TOKEN not in str(error.value)
    assert aioclient_mock.call_count == 1
    assert str(aioclient_mock.mock_calls[0][1]) == API_DEVICES_URL


# Extended by Step 7 coverage audit.
# Value: protects=rejection of malformed schedules, layouts and timestamps;
# fails_when=setting type, range, uniqueness or timezone guards are removed;
# why_new=existing malformed snapshot cases only cover identity and measurements;
# seam=none; no_claim=backend validation or device application (mocked HTTP)
@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update(account_id=42),
        lambda data: data.update(devices="not-a-list"),
        lambda data: data["devices"].append(deepcopy(data["devices"][0])),
        lambda data: data["devices"][0].update(temperature_c="warm"),
        lambda data: data["devices"][0]["sleep_hours"].update(enabled=1),
        lambda data: data["devices"][0]["sleep_hours"].update(start_minute=True),
        lambda data: data["devices"][0]["sleep_hours"].update(end_minute=1440),
        lambda data: data["devices"][0]["sleep_hours"].update(end_minute=74),
        lambda data: data["devices"][0]["sleep_hours"].update(end_minute=781),
        lambda data: data["devices"][0]["sleep_hours"].update(timezone_id=""),
        lambda data: data["devices"][0].update(layout_id=True),
        lambda data: data["layouts"][0].update(id=0),
        lambda data: data["layouts"][1].update(id=1),
        lambda data: data["layouts"][1].update(name="Complete"),
        lambda data: data["layouts"][0].update(name=""),
        lambda data: data["devices"][0].update(received_at="2026-07-24T08:00:00"),
        lambda data: data["devices"][0].update(quiet_until="not a timestamp"),
    ],
)
async def test_api_rejects_non_authoritative_snapshots(
    hass, aioclient_mock, mutate
) -> None:
    """Malformed or ambiguous snapshots never reach entities."""
    body = payload()
    mutate(body)
    aioclient_mock.get(API_DEVICES_URL, json=body)
    client = StratusDashApiClient(async_get_clientsession(hass), TOKEN)
    with pytest.raises(StratusDashInvalidResponseError):
        await client.async_get_devices()


@pytest.mark.parametrize("body", ["not json", "[]", "null"])
async def test_api_rejects_invalid_json_objects(hass, aioclient_mock, body):
    aioclient_mock.get(API_DEVICES_URL, text=body)
    client = StratusDashApiClient(async_get_clientsession(hass), TOKEN)
    with pytest.raises(StratusDashInvalidResponseError):
        await client.async_get_devices()


async def test_api_rejects_bad_token_without_network(hass, aioclient_mock) -> None:
    """Malformed bearer values cannot become request headers."""
    client = StratusDashApiClient(
        async_get_clientsession(hass),
        "sdha_bad\r\nInjected: value",
    )
    with pytest.raises(StratusDashAuthenticationError):
        await client.async_get_devices()
    assert aioclient_mock.call_count == 0


async def test_client_reads_delayed_chunks_and_enforces_stream_limit(
    hass, aioclient_mock
):
    body = json.dumps(payload()).encode()
    stream = StreamReader(Mock(_reading_paused=False), limit=65536)
    stream.feed_data(body[:10])

    async def finish_response():
        await asyncio.sleep(0)
        stream.feed_data(body[10:])
        stream.feed_eof()

    task = asyncio.create_task(finish_response())
    aioclient_mock.get(API_DEVICES_URL, json=payload())
    client = StratusDashApiClient(async_get_clientsession(hass), TOKEN)
    with patch.object(
        AiohttpClientMockResponse,
        "content",
        new_callable=PropertyMock,
        return_value=stream,
    ):
        assert (await client.async_get_devices()).devices[0].temperature_c == 21.5
    await task

    aioclient_mock.clear_requests()
    aioclient_mock.get(API_DEVICES_URL, text=" " * 1_048_577)
    with pytest.raises(StratusDashInvalidResponseError, match="too much data"):
        await client.async_get_devices()


async def test_config_flow_and_wrong_account_reauth(hass, aioclient_mock) -> None:
    """Setup deduplicates accounts and reauth cannot switch account identity."""
    aioclient_mock.get(API_DEVICES_URL, json=payload(devices=[]))
    created = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={CONF_TOKEN: TOKEN},
    )
    assert created["type"] is FlowResultType.CREATE_ENTRY
    assert created["result"].unique_id == ACCOUNT_ID

    entry = created["result"]
    flow = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        API_DEVICES_URL,
        json=payload(account_id=OTHER_ACCOUNT_ID, devices=[]),
    )
    rejected = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_TOKEN: NEW_TOKEN}
    )
    assert rejected["type"] is FlowResultType.ABORT
    assert rejected["reason"] == "wrong_account"
    assert entry.data[CONF_TOKEN] == TOKEN


async def test_coordinator_failure_and_recovery(
    hass, aioclient_mock, config_entry
) -> None:
    """Temporary failures mark entities unavailable and a valid poll recovers."""
    aioclient_mock.get(API_DEVICES_URL, json=payload())
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data.coordinator
    registry = er.async_get(hass)
    unique_id = f"{ACCOUNT_ID}_{DEVICE_ID}_temperature"
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "21.5"

    aioclient_mock.clear_requests()
    aioclient_mock.get(API_DEVICES_URL, status=503)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not coordinator.last_update_success
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    recovered = payload()
    recovered["devices"][0]["temperature_c"] = 22.25
    aioclient_mock.clear_requests()
    aioclient_mock.get(API_DEVICES_URL, json=recovered)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success
    assert hass.states.get(entity_id).state == "22.25"


# Extended by Step 7 coverage audit.
# Value: protects=all eight entity identities survive device removal, rename and reload;
# fails_when=rediscovery duplicates entities or removed devices retain usable controls;
# why_new=existing discovery checks only one sensor becoming stale;
# seam=none; no_claim=real ownership changes (snapshots are mocked)
async def test_dynamic_discovery_and_device_availability(
    hass, aioclient_mock, config_entry
) -> None:
    """A later-owned device is discovered and stale data becomes unavailable."""
    aioclient_mock.get(API_DEVICES_URL, json=payload(devices=[]))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data.coordinator
    registry = er.async_get(hass)
    unique_id = f"{ACCOUNT_ID}_{DEVICE_ID}_humidity"
    assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None

    aioclient_mock.clear_requests()
    aioclient_mock.get(API_DEVICES_URL, json=payload())
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "48.0"

    stale = payload()
    stale["devices"][0]["available"] = False
    aioclient_mock.clear_requests()
    aioclient_mock.get(API_DEVICES_URL, json=stale)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    keys = {
        "sensor": ("temperature", "humidity", "pressure", "battery_voltage"),
        "switch": ("quiet_hours",),
        "time": ("quiet_hours_start", "quiet_hours_end"),
        "select": ("layout",),
    }
    identities = {
        (domain, key): registry.async_get_entity_id(
            domain, DOMAIN, f"{ACCOUNT_ID}_{DEVICE_ID}_{key}"
        )
        for domain, domain_keys in keys.items()
        for key in domain_keys
    }
    assert all(identities.values())
    aioclient_mock.clear_requests()
    aioclient_mock.get(API_DEVICES_URL, json=payload(devices=[]))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert all(
        hass.states.get(entity).state == STATE_UNAVAILABLE
        for entity in identities.values()
    )

    renamed = payload()
    renamed["devices"][0]["name"] = "Bedroom"
    aioclient_mock.clear_requests()
    aioclient_mock.get(API_DEVICES_URL, json=renamed)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(er.async_entries_for_config_entry(registry, config_entry.entry_id)) == 8
    for (domain, key), entity in identities.items():
        assert (
            registry.async_get_entity_id(
                domain, DOMAIN, f"{ACCOUNT_ID}_{DEVICE_ID}_{key}"
            )
            == entity
        )
        assert hass.states.get(entity).state != STATE_UNAVAILABLE
    assert hass.states.get(identities["sensor", "humidity"]).state == "48.0"


async def test_setup_auth_failure_starts_reauth(hass, aioclient_mock) -> None:
    """A revoked token enters Home Assistant's reauthentication flow."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="StratusDash",
        data={CONF_TOKEN: TOKEN},
        unique_id=ACCOUNT_ID,
    )
    entry.add_to_hass(hass)
    aioclient_mock.get(API_DEVICES_URL, status=401)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN)
