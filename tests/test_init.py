"""Tests for setting up and unloading the Tody integration."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.tody.api import TodyAuthError, TodyConnectionError
from custom_components.tody.const import CONF_REFRESH_TOKEN, DOMAIN

from .conftest import MASTERDATA_ID


async def test_setup_and_unload(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """The entry loads, fetches the right sync and unloads cleanly."""
    entry = setup_integration
    assert entry.state is ConfigEntryState.LOADED
    mock_client.fetch_snapshot.assert_awaited_with(MASTERDATA_ID)
    _, kwargs = mock_client.coordinator_cls.call_args
    assert kwargs["refresh_token"] == "refresh-1"
    assert entry.runtime_data.update_interval == timedelta(minutes=15)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_connection_error(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A connection error on first refresh retries setup."""
    mock_client.fetch_snapshot.side_effect = TodyConnectionError
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_auth_error_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """An auth error on first refresh fails setup and starts a reauth flow."""
    mock_client.fetch_snapshot.side_effect = TodyAuthError
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH
    assert flows[0]["context"]["entry_id"] == config_entry.entry_id


async def test_auth_error_during_polling(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_client: MagicMock, freezer
) -> None:
    """Losing access while running makes entities unavailable and starts reauth."""
    mock_client.fetch_snapshot.side_effect = TodyAuthError
    freezer.tick(timedelta(minutes=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get("todo.tody_all_tasks").state == STATE_UNAVAILABLE
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH


async def test_refresh_token_persisted(
    hass: HomeAssistant, setup_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A rotated refresh token from the client is stored in the entry."""
    _, kwargs = mock_client.coordinator_cls.call_args
    kwargs["on_refresh_token"]("refresh-rotated")
    assert setup_integration.data[CONF_REFRESH_TOKEN] == "refresh-rotated"
