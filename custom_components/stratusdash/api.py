"""Small client for the StratusDash Home Assistant API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
import math
import re
from uuid import UUID

import aiohttp
from homeassistant.util import dt as dt_util

from .const import API_BASE_URL

_TOKEN_PATTERN = re.compile(r"sdha_[A-Za-z0-9_-]{43}")
_MAX_RESPONSE_BYTES = 1_048_576


class StratusDashApiError(Exception):
    """Base API error whose message never contains credentials or response data."""


class StratusDashAuthenticationError(StratusDashApiError):
    """The credential is invalid or revoked."""


class StratusDashConnectionError(StratusDashApiError):
    """The service could not be reached."""


class StratusDashInvalidResponseError(StratusDashApiError):
    """The service returned an invalid response."""


class StratusDashControlError(StratusDashApiError):
    """The requested settings are invalid or no longer available."""


@dataclass(frozen=True, slots=True)
class SleepHours:
    enabled: bool
    start_minute: int
    end_minute: int
    timezone_id: str


@dataclass(frozen=True, slots=True)
class Layout:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class StratusDashDevice:
    """One owned device and its latest measurements."""

    id: str
    name: str
    available: bool
    temperature_c: float | None
    humidity_percent: float | None
    pressure_hpa: float | None
    battery_voltage_v: float | None
    received_at: datetime | None
    sleep_hours: SleepHours
    quiet_until: datetime | None
    layout_id: int | None


@dataclass(frozen=True, slots=True)
class StratusDashData:
    """A complete account snapshot."""

    account_id: str
    devices: tuple[StratusDashDevice, ...]
    layouts: tuple[Layout, ...]

    def device(self, device_id: str) -> StratusDashDevice | None:
        """Return a device by its stable public ID."""
        return next((item for item in self.devices if item.id == device_id), None)


class StratusDashApiClient:
    """Fetch account data with Home Assistant's shared HTTP session."""

    def __init__(self, session: aiohttp.ClientSession, token: str) -> None:
        self._session = session
        self._token = token

    async def async_get_devices(self) -> StratusDashData:
        """Fetch and validate the owned-device snapshot."""
        return _parse_data(await self._request("GET", "/devices"))

    async def async_get_history(self, start: datetime) -> dict:
        """Fetch hourly aggregates; validate them before recorder import."""
        body = await self._request(
            "GET", "/history", params={"start": start.isoformat()}
        )
        try:
            return json.loads(body)
        except ValueError, UnicodeDecodeError:
            raise StratusDashInvalidResponseError(
                "Invalid StratusDash history"
            ) from None

    async def async_set_sleep_hours(self, device_id: str, **changes) -> None:
        await self._request("PATCH", f"/devices/{device_id}/sleep-hours", json=changes)

    async def async_set_layout(self, device_id: str, layout_id: int) -> None:
        await self._request(
            "PUT", f"/devices/{device_id}/layout", json={"layout_id": layout_id}
        )

    async def _request(self, method: str, path: str, **kwargs) -> bytes:
        if _TOKEN_PATTERN.fullmatch(self._token) is None:
            raise StratusDashAuthenticationError("Invalid StratusDash credential")
        try:
            async with asyncio.timeout(15):
                async with self._session.request(
                    method,
                    f"{API_BASE_URL}{path}",
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {self._token}",
                    },
                    allow_redirects=False,
                    **kwargs,
                ) as response:
                    if response.status == 401:
                        raise StratusDashAuthenticationError(
                            "Invalid StratusDash credential"
                        )
                    if response.status in (404, 422):
                        raise StratusDashControlError(
                            "The device, layout or quiet-hours schedule is invalid"
                        )
                    if response.status == 204:
                        return b""
                    if response.status != 200:
                        raise StratusDashConnectionError(
                            "StratusDash is temporarily unavailable"
                        )
                    content_length = getattr(response, "content_length", None)
                    if content_length is None:
                        try:
                            content_length = int(response.headers.get("Content-Length"))
                        except TypeError, ValueError:
                            content_length = None
                    if (
                        content_length is not None
                        and content_length > _MAX_RESPONSE_BYTES
                    ):
                        raise StratusDashInvalidResponseError(
                            "StratusDash returned too much data"
                        )
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        body.extend(chunk)
                        if len(body) > _MAX_RESPONSE_BYTES:
                            raise StratusDashInvalidResponseError(
                                "StratusDash returned too much data"
                            )
        except StratusDashApiError:
            raise
        except TimeoutError, aiohttp.ClientError:
            raise StratusDashConnectionError(
                "Could not connect to StratusDash"
            ) from None
        return bytes(body)


def _parse_data(body: bytes) -> StratusDashData:
    """Parse only the small public contract used by the entities."""
    try:
        payload = json.loads(body.decode("utf-8"))
        account_id = str(UUID(payload["account_id"]))
        raw_devices = payload["devices"]
        if not isinstance(raw_devices, list):
            raise TypeError
        devices: list[StratusDashDevice] = []
        seen: set[str] = set()
        for raw in raw_devices:
            device_id = str(UUID(raw["id"]))
            if device_id in seen:
                raise ValueError
            seen.add(device_id)
            name = raw["name"]
            available = raw["available"]
            if not isinstance(name, str) or not name or not isinstance(available, bool):
                raise TypeError
            schedule = raw["sleep_hours"]
            enabled = schedule["enabled"]
            start = _minute(schedule["start_minute"])
            end = _minute(schedule["end_minute"])
            zone = schedule["timezone_id"]
            if (
                not isinstance(enabled, bool)
                or not isinstance(zone, str)
                or not zone
                or not 15 <= (end - start) % 1440 <= 720
            ):
                raise ValueError
            layout_id = raw["layout_id"]
            if layout_id is not None:
                layout_id = _layout_id(layout_id)
            devices.append(
                StratusDashDevice(
                    id=device_id,
                    name=name,
                    available=available,
                    temperature_c=_number(raw.get("temperature_c")),
                    humidity_percent=_number(raw.get("humidity_percent")),
                    pressure_hpa=_number(raw.get("pressure_hpa")),
                    battery_voltage_v=_number(raw.get("battery_voltage_v")),
                    received_at=_timestamp(raw.get("received_at")),
                    sleep_hours=SleepHours(enabled, start, end, zone),
                    quiet_until=_timestamp(raw.get("quiet_until")),
                    layout_id=layout_id,
                )
            )
        layouts = tuple(
            Layout(_layout_id(item["id"]), item["name"]) for item in payload["layouts"]
        )
        if (
            any(not isinstance(item.name, str) or not item.name for item in layouts)
            or len({item.id for item in layouts}) != len(layouts)
            or len({item.name for item in layouts}) != len(layouts)
        ):
            raise ValueError
    except KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError:
        raise StratusDashInvalidResponseError(
            "StratusDash returned invalid data"
        ) from None
    return StratusDashData(
        account_id=account_id, devices=tuple(devices), layouts=layouts
    )


def _minute(value: object) -> int:
    if type(value) is not int or not 0 <= value < 1440:
        raise ValueError
    return value


def _layout_id(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError
    return value


def _timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or (parsed := dt_util.parse_datetime(value)) is None
        or parsed.tzinfo is None
    ):
        raise ValueError
    return dt_util.as_utc(parsed)


def _number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError
    try:
        result = float(value)
    except OverflowError:
        raise ValueError from None
    if not math.isfinite(result):
        raise ValueError
    return result
