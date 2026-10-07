"""Tests for the Tody config, reauth and options flows."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tody.api import JoinResult, TodyConnectionError, TodyInviteError
from custom_components.tody.const import (
    CONF_INVITE_CODE,
    CONF_LOOKAHEAD_DAYS,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

from .conftest import ENTRY_DATA, MASTERDATA_ID


async def test_user_flow_success(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """A valid invite code creates an entry titled after the sync."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_INVITE_CODE: " ABC123 "})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Our House"
    assert result["data"] == ENTRY_DATA
    assert result["result"].unique_id == MASTERDATA_ID
    mock_client.join.assert_awaited_once_with("ABC123")


async def test_user_flow_title_fallback(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """If the sync name can't be read, the title falls back to Tody."""
    mock_client.fetch_snapshot.side_effect = TodyConnectionError
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_INVITE_CODE: "ABC"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Tody"


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (TodyInviteError, "invalid_code"),
        (TodyConnectionError, "cannot_connect"),
        (RuntimeError, "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, mock_client: MagicMock, side_effect: type[Exception], error: str
) -> None:
    """Join errors are shown and the flow can recover."""
    mock_client.join.side_effect = side_effect
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_INVITE_CODE: "BAD"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_client.join.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_INVITE_CODE: "GOOD"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Joining the same data sync twice aborts."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_INVITE_CODE: "ABC"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_success(
    hass: HomeAssistant, mock_client: MagicMock, setup_integration: MockConfigEntry
) -> None:
    """Reauth with a code for the same sync updates the stored credentials."""
    entry = setup_integration
    mock_client.join.return_value = JoinResult(
        uid="uid-2",
        refresh_token="refresh-2",
        masterdata_id=MASTERDATA_ID,
        participant_id="p-hass",
        participant_name="Hass",
    )
    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_INVITE_CODE: "NEW"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == "refresh-2"
    assert entry.data["uid"] == "uid-2"


async def test_reauth_error_then_wrong_sync(
    hass: HomeAssistant, mock_client: MagicMock, setup_integration: MockConfigEntry
) -> None:
    """Reauth shows join errors, and refuses a code for another data sync."""
    entry = setup_integration
    result = await entry.start_reauth_flow(hass)

    mock_client.join.side_effect = TodyInviteError
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_INVITE_CODE: "BAD"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_code"}

    mock_client.join.side_effect = None
    mock_client.join.return_value = JoinResult(
        uid="uid-3", refresh_token="refresh-3", masterdata_id="other-sync", participant_id="p", participant_name="X"
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_INVITE_CODE: "OTHER"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_sync"
    assert entry.data[CONF_REFRESH_TOKEN] == "refresh-1"


async def test_options_flow(hass: HomeAssistant, mock_client: MagicMock, setup_integration: MockConfigEntry) -> None:
    """Options are stored and the entry reloaded with the new interval."""
    entry = setup_integration
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_LOOKAHEAD_DAYS: 3, CONF_SCAN_INTERVAL: 30}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_LOOKAHEAD_DAYS: 3, CONF_SCAN_INTERVAL: 30}
    assert entry.runtime_data.update_interval.total_seconds() == 30 * 60
    # Look-ahead of 3 days now includes "Windows".
    state = hass.states.get("todo.our_house_all_tasks")
    assert state.state == "6"


async def test_options_flow_out_of_range(
    hass: HomeAssistant, mock_client: MagicMock, setup_integration: MockConfigEntry
) -> None:
    """Out-of-range options are rejected by the schema."""
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    with pytest.raises(Exception):  # noqa: B017 - schema invalid error type varies (voluptuous/probatio)
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_LOOKAHEAD_DAYS: 8, CONF_SCAN_INTERVAL: 15}
        )


async def test_user_flow_aborts_without_api_key(hass) -> None:
    """With the placeholder key the flow stops before contacting Tody."""
    with (
        patch("custom_components.tody.config_flow.FIREBASE_API_KEY", "__FIREBASE_API_KEY__"),
        patch("custom_components.tody.config_flow.TodyClient") as client,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "api_key_missing"
    client.assert_not_called()
