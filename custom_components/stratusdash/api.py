"""Small client for the read-only StratusDash Home Assistant API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import math
import re
from uuid import UUID

import aiohttp

from .const import API_DEVICES_URL

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


@dataclass(frozen=True, slots=True)
class StratusDashData:
    """A complete account snapshot."""

    account_id: str
    devices: tuple[StratusDashDevice, ...]

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
        if _TOKEN_PATTERN.fullmatch(self._token) is None:
            raise StratusDashAuthenticationError("Invalid StratusDash credential")
        try:
            async with asyncio.timeout(15):
                async with self._session.get(
                    API_DEVICES_URL,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {self._token}",
                    },
                    allow_redirects=False,
                ) as response:
                    if response.status == 401:
                        raise StratusDashAuthenticationError(
                            "Invalid StratusDash credential"
                        )
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
                    body = await response.content.read(_MAX_RESPONSE_BYTES + 1)
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
        return _parse_data(body)


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
            devices.append(
                StratusDashDevice(
                    id=device_id,
                    name=name,
                    available=available,
                    temperature_c=_number(raw.get("temperature_c")),
                    humidity_percent=_number(raw.get("humidity_percent")),
                    pressure_hpa=_number(raw.get("pressure_hpa")),
                    battery_voltage_v=_number(raw.get("battery_voltage_v")),
                )
            )
    except KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError:
        raise StratusDashInvalidResponseError(
            "StratusDash returned invalid data"
        ) from None
    return StratusDashData(account_id=account_id, devices=tuple(devices))


def _number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError
    result = float(value)
    if not math.isfinite(result):
        raise ValueError
    return result
