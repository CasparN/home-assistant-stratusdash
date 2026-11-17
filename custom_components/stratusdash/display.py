"""Device-specific sensor selections stored in the Home Assistant config entry."""

import math
import unicodedata

from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

DISPLAY_SENSORS = "display_sensors"
DOMAINS = ("sensor", "number", "input_number")


def numeric_value(raw: str) -> float | None:
    try:
        value = float(raw)
    except ValueError, OverflowError:
        return None
    return value if math.isfinite(value) else None


def selectable(state: State) -> bool:
    if state.domain not in DOMAINS:
        return False
    if numeric_value(state.state) is not None:
        return True
    return state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE) and (
        state.domain in ("number", "input_number")
        or ATTR_UNIT_OF_MEASUREMENT in state.attributes
        or "state_class" in state.attributes
    )


def display_text(raw: str, limit: int) -> str:
    raw = raw.translate(
        str.maketrans(
            {"µ": "u", "μ": "u", "°": "~", "€": "EUR", "²": "2", "³": "3", "₂": "2"}
        )
    )
    text = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    return " ".join(
        "".join(c for c in text if c.isprintable() and c not in "{}").split()
    )[:limit].strip()


def sensor_refs(hass: HomeAssistant, entity_ids: list[str]) -> list[str]:
    registry = er.async_get(hass)
    return [
        entry.id if (entry := registry.async_get(entity_id)) else entity_id
        for entity_id in entity_ids
    ]


def sensor_ids(hass: HomeAssistant, refs: list[str]) -> list[str | None]:
    registry = er.async_get(hass)
    return [er.async_resolve_entity_id(registry, ref) for ref in refs]


def display_items(hass: HomeAssistant, refs: list[str]) -> list[dict]:
    items = []
    for slot, entity_id in enumerate(sensor_ids(hass, refs), start=1):
        state = hass.states.get(entity_id) if entity_id else None
        label = display_text(state.name, 24) if state else ""
        unit = (
            display_text(str(state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) or ""), 8)
            if state
            else ""
        )
        items.append(
            {
                "name": label or f"Sensor {slot}",
                "value": numeric_value(state.state) if state else None,
                "unit": unit,
            }
        )
    return items
