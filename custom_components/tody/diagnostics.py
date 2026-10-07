"""Diagnostics for the Tody integration: the raw Tody data from the last poll, without secrets or names."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_REFRESH_TOKEN, CONF_UID
from .coordinator import TodyConfigEntry

TO_REDACT = {
    CONF_REFRESH_TOKEN,
    CONF_UID,
    "authUserIDs",
    "loginMail",
    "userName",
    "inviteParticipantName",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: TodyConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": {"data": async_redact_data(dict(entry.data), TO_REDACT), "options": dict(entry.options)},
        "snapshot": async_redact_data(coordinator.snapshot or {}, TO_REDACT),
    }
