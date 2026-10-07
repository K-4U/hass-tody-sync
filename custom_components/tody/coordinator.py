"""Data update coordinator for the Tody integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import model
from .api import TodyAuthError, TodyClient, TodyConnectionError
from .const import (
    CONF_MASTERDATA_ID,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .model import TodyData

_LOGGER = logging.getLogger(__name__)

type TodyConfigEntry = ConfigEntry[TodyCoordinator]


class TodyCoordinator(DataUpdateCoordinator[TodyData]):
    """Polls the Tody data sync and parses it into TodyData."""

    config_entry: TodyConfigEntry
    main_device_id: str

    def __init__(self, hass: HomeAssistant, entry: TodyConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
        )
        self.masterdata_id: str = entry.data[CONF_MASTERDATA_ID]
        self.client = TodyClient(
            async_get_clientsession(hass),
            refresh_token=entry.data[CONF_REFRESH_TOKEN],
            on_refresh_token=self._async_store_refresh_token,
        )

    @callback
    def _async_store_refresh_token(self, refresh_token: str) -> None:
        """Persist a rotated refresh token in the config entry."""
        entry = self.config_entry
        if entry.data.get(CONF_REFRESH_TOKEN) == refresh_token:
            return
        self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_REFRESH_TOKEN: refresh_token})

    async def _async_update_data(self) -> TodyData:
        """Fetch and parse the data sync."""
        try:
            snapshot = await self.client.fetch_snapshot(self.masterdata_id)
        except TodyAuthError as err:
            raise ConfigEntryAuthFailed(str(err) or "Access to the Tody data sync was lost") from err
        except TodyConnectionError as err:
            raise UpdateFailed(f"Error communicating with Tody: {err}") from err
        return model.parse_snapshot(snapshot, now=dt_util.utcnow(), tz=dt_util.get_default_time_zone())
