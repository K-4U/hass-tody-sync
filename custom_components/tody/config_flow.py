"""Config, reauth and options flows for the Tody integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from homeassistant.helpers.selector import AreaSelector

from .api import JoinResult, TodyClient, TodyConnectionError, TodyInviteError
from .areas import link_ha_area, linked_ha_area, match_ha_area
from .const import (
    CONF_AUTO_LINKED_AREAS,
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
    """Look-ahead window, polling interval and links from Tody areas to HA areas."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        self._options: dict[str, Any] = {}
        # Form field label (the Tody area name) -> Tody area id
        self._area_fields: dict[str, str] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Look-ahead and polling interval."""
        if user_input is not None:
            self._options = user_input
            return await self.async_step_areas()

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

    async def async_step_areas(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a Home Assistant area per Tody area (stored as the area list's entity area)."""
        entry = self.config_entry
        masterdata_id = entry.data[CONF_MASTERDATA_ID]
        if user_input is not None:
            for label, tody_area_id in self._area_fields.items():
                link_ha_area(self.hass, masterdata_id, tody_area_id, user_input.get(label))
            return self._async_save()

        coordinator = getattr(entry, "runtime_data", None)
        data = coordinator.data if coordinator else None
        if data is None or not data.areas:
            return self._async_save()

        fields: dict[Any, Any] = {}
        for area in sorted(data.areas.values(), key=lambda area: area.name.casefold()):
            label = area.name
            while label in self._area_fields:
                label += " "
            self._area_fields[label] = area.id
            current = linked_ha_area(self.hass, masterdata_id, area.id)
            if current is None and area.id not in entry.options.get(CONF_AUTO_LINKED_AREAS, []):
                current = match_ha_area(self.hass, area.name)
            fields[vol.Optional(label, description={"suggested_value": current})] = AreaSelector()
        return self.async_show_form(step_id="areas", data_schema=vol.Schema(fields))

    @callback
    def _async_save(self) -> ConfigFlowResult:
        # Keep options this flow doesn't edit (e.g. which areas were auto-linked already).
        return self.async_create_entry(data={**self.config_entry.options, **self._options})
