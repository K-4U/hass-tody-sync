"""Config, reauth and options flows for the Tody integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import JoinResult, TodyClient, TodyConnectionError, TodyInviteError
from .const import (
    CONF_INVITE_CODE,
    CONF_LOOKAHEAD_DAYS,
    CONF_MASTERDATA_ID,
    CONF_PARTICIPANT_ID,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_UID,
    DEFAULT_LOOKAHEAD_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_NAME,
    DOMAIN,
    FIREBASE_API_KEY,
    MAX_LOOKAHEAD_DAYS,
    MAX_SCAN_INTERVAL,
    MIN_LOOKAHEAD_DAYS,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

INVITE_SCHEMA = vol.Schema({vol.Required(CONF_INVITE_CODE): str})


class TodyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Join a Tody data sync with an invite code."""

    VERSION = 1

    async def _async_join(self, invite_code: str) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Join the sync; returns (entry data, errors)."""
        tokens: list[str] = []
        client = TodyClient(async_get_clientsession(self.hass), on_refresh_token=tokens.append)
        try:
            result: JoinResult = await client.join(invite_code.strip())
        except TodyInviteError:
            return None, {"base": "invalid_code"}
        except TodyConnectionError:
            return None, {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected error joining Tody data sync")
            return None, {"base": "unknown"}

        data = {
            CONF_UID: result.uid,
            CONF_REFRESH_TOKEN: tokens[-1] if tokens else result.refresh_token,
            CONF_MASTERDATA_ID: result.masterdata_id,
            CONF_PARTICIPANT_ID: result.participant_id,
        }
        return data, {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the invite code."""
        if FIREBASE_API_KEY.startswith("__"):
            return self.async_abort(reason="api_key_missing")
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = await self._async_join(user_input[CONF_INVITE_CODE])
            if data is not None:
                await self.async_set_unique_id(data[CONF_MASTERDATA_ID])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=DEVICE_NAME, data=data)
        return self.async_show_form(step_id="user", data_schema=INVITE_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Access was lost; ask for a fresh invite code."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Re-join with a new invite code for the same data sync."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = await self._async_join(user_input[CONF_INVITE_CODE])
            if data is not None:
                await self.async_set_unique_id(data[CONF_MASTERDATA_ID])
                self._abort_if_unique_id_mismatch(reason="wrong_sync")
                return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=data)
        return self.async_show_form(step_id="reauth_confirm", data_schema=INVITE_SCHEMA, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TodyOptionsFlow:
        """Create the options flow."""
        return TodyOptionsFlow()


class TodyOptionsFlow(OptionsFlowWithReload):
    """Look-ahead window and polling interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_LOOKAHEAD_DAYS,
                    default=options.get(CONF_LOOKAHEAD_DAYS, DEFAULT_LOOKAHEAD_DAYS),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_LOOKAHEAD_DAYS, max=MAX_LOOKAHEAD_DAYS)),
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
