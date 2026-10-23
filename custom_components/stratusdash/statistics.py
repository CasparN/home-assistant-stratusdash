"""Import captured-hour statistics without replaying live sensor states."""

from collections import defaultdict
from datetime import datetime
from uuid import UUID

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.util import dt as dt_util

from .api import (
    StratusDashAuthenticationError,
    StratusDashData,
    StratusDashInvalidResponseError,
    _number,
    _timestamp,
)
from .const import DOMAIN

METRICS = {
    "temperature": ("temperature_c", "Temperature", "°C", "temperature"),
    "humidity": ("humidity_percent", "Humidity", "%", None),
    "pressure": ("pressure_hpa", "Atmospheric pressure", "hPa", "pressure"),
    "battery_voltage": (
        "battery_voltage_v",
        "Battery voltage",
        "V",
        "voltage",
    ),
}


def statistic_id(account_id: str, device_id: str, key: str) -> str:
    return f"{DOMAIN}:{account_id.replace('-', '')}_{device_id.replace('-', '')}_{key}"


def async_import_history(
    hass, data: StratusDashData, payload: dict, start: datetime
) -> datetime:
    """Validate the entire response before queuing any recorder writes."""
    if isinstance(payload, dict) and payload.get("account_id") != data.account_id:
        raise StratusDashAuthenticationError("History belongs to a different account")
    grouped = defaultdict(list)
    try:
        end = _timestamp(payload["end"])
        if (
            _timestamp(payload["start"]) != start
            or end is None
            or end != end.replace(minute=0, second=0, microsecond=0)
            or not start < end <= dt_util.utcnow()
            or not isinstance(payload["hours"], list)
        ):
            raise ValueError
        seen = set()
        for row in payload["hours"]:
            device_id = str(UUID(row["device_id"]))
            hour = _timestamp(row["start"])
            if (
                hour is None
                or hour != hour.replace(minute=0, second=0, microsecond=0)
                or not start <= hour < end
                or (device_id, hour) in seen
            ):
                raise ValueError
            seen.add((device_id, hour))
            if data.device(device_id) is None:
                continue  # Ownership may change between snapshot and history requests.
            for key, (field, _name, _unit, _unit_class) in METRICS.items():
                if (values := row[field]) is None:
                    continue
                minimum, maximum, mean = (
                    _number(values[name]) for name in ("min", "max", "mean")
                )
                if (
                    minimum is None
                    or maximum is None
                    or mean is None
                    or not minimum <= mean <= maximum
                ):
                    raise ValueError
                grouped[(device_id, key)].append(
                    {"start": hour, "min": minimum, "max": maximum, "mean": mean}
                )
    except KeyError, TypeError, ValueError, AttributeError:
        raise StratusDashInvalidResponseError("Invalid StratusDash history") from None

    for (device_id, key), rows in grouped.items():
        _field, name, unit, unit_class = METRICS[key]
        async_add_external_statistics(
            hass,
            {
                "statistic_id": statistic_id(data.account_id, device_id, key),
                "source": DOMAIN,
                "name": f"{data.device(device_id).name} {name} history",
                "unit_of_measurement": unit,
                "unit_class": unit_class,
                "mean_type": StatisticMeanType.ARITHMETIC,
                "has_sum": False,
            },
            sorted(rows, key=lambda row: row["start"]),
        )
    return end
