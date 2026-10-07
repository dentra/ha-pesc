"""Data update coordinator"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import const, pesc_api, pesc_client

_LOGGER = logging.getLogger(__name__)


# https://developers.home-assistant.io/docs/integration_fetching_data/#polling-api-endpoints
class PescDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        super().__init__(
            hass,
            _LOGGER,
            name=const.DOMAIN,
            update_interval=const.DEFAULT_UPDATE_INTERVAL,
        )

        _LOGGER.debug("Initialize updater for %s", entry.title)

        self.api = pesc_api.PescApi(
            pesc_client.PescClient(
                async_get_clientsession(hass), entry.data[const.CONF_AUTH]
            )
        )

        if const.CONF_UPDATE_INTERVAL in entry.options:
            self.update_interval = cv.time_period(
                entry.options[const.CONF_UPDATE_INTERVAL]
            )

    @override
    async def _async_update_data(self):
        try:
            await self.async_with_relogin(self._fetch)
        except pesc_client.ClientAuthError as err:
            _LOGGER.debug("ClientAuthError: %s", err)
            # Raising ConfigEntryAuthFailed will cancel future updates
            # and start a config flow with SOURCE_REAUTH (async_step_reauth)
            raise ConfigEntryAuthFailed from err
        except pesc_client.ClientError as err:
            _LOGGER.error("Ошибка вызова API: %s", err)
            raise UpdateFailed(f"Ошибка вызова API: {err}") from err

    async def async_with_relogin[T](self, call: Callable[[], Awaitable[T]]) -> T:
        """Call API, on auth error relogin once with the saved password."""
        try:
            return await call()
        except pesc_client.ClientAuthError:
            if const.CONF_PASSWORD not in self.config_entry.data:
                raise
        await self._relogin()
        return await call()

    async def _fetch(self):
        async with asyncio.timeout(60):
            await self.api.async_fetch_all()

    async def _relogin(self):
        auth = await self.api.async_relogin(
            username=self.config_entry.data[const.CONF_USERNAME],
            password=self.config_entry.data[const.CONF_PASSWORD],
            auth=self.config_entry.data[const.CONF_AUTH],
            login_type=self.config_entry.data[const.CONF_LOGIN_TYPE],
        )
        data = {
            **self.config_entry.data,
            const.CONF_AUTH: self.config_entry.data.get(const.CONF_AUTH) | auth,
        }
        self.hass.config_entries.async_update_entry(self.config_entry, data=data)
