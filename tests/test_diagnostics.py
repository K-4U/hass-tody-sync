"""Diagnostics must not leak logins or names."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator


async def test_diagnostics_redacted(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    setup_integration: MockConfigEntry,
) -> None:
    """Refresh token, anonymous uids, login email and participant names are redacted."""
    mock_client.fetch_snapshot.return_value = {
        "users": [{"_id": "p-1", "userName": "Somebody", "authUserIDs": ["anon"]}],
        "fbMetadata": [{"_id": "meta", "loginMail": "someone@example.com"}],
        "tasks": [{"_id": "t-1", "taskName": "Vacuum", "createdDate": datetime(2026, 1, 1, tzinfo=UTC)}],
    }
    await setup_integration.runtime_data.async_refresh()

    diag = await get_diagnostics_for_config_entry(hass, hass_client, setup_integration)
    text = str(diag)
    assert "refresh-1" not in text and "uid-1" not in text
    assert "Somebody" not in text and "someone@example.com" not in text and "'anon'" not in text
    assert diag["snapshot"]["tasks"][0]["taskName"] == "Vacuum"
    assert diag["snapshot"]["users"][0]["userName"] == "**REDACTED**"
