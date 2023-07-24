import logging
from datetime import timedelta
from typing import Final
import async_timeout

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import pesc_api, pesc_client, const

_LOGGER = logging.getLogger(__name__)


PLATFORMS: Final = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""

    _LOGGER.debug("Setup %s (%s)", entry.title, entry.data[const.CONF_USERNAME])

    hass.data.setdefault(const.DOMAIN, {})

    coordinator = PescDataUpdateCoordinator(hass, entry)
    hass.data[const.DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_config_entry_first_refresh()

    # add options handler
    if not entry.update_listeners:
        entry.add_update_listener(async_update_options)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry):
    """Update from a config entry options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unload %s (%s)", entry.title, entry.data[const.CONF_USERNAME])
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[const.DOMAIN].pop(entry.entry_id)
    return unload_ok


# https://developers.home-assistant.io/docs/integration_fetching_data/#polling-api-endpoints
class PescDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        super().__init__(
            hass,
            _LOGGER,
            name=const.DOMAIN,
            update_interval=const.DEFAULT_UPDATE_INTERVAL,
        )

        self.api = pesc_api.PescApi(
            pesc_client.PescClient(
                async_get_clientsession(hass), entry.data[const.CONF_TOKEN]
            )
        )

        if const.CONF_UPDATE_INTERVAL in entry.options:
            self.update_interval = cv.time_period(
                entry.options[const.CONF_UPDATE_INTERVAL]
            )

    async def _async_update_data(self):
        try:
            # asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with async_timeout.timeout(60):
                await self.api.async_fetch_all()
        except pesc_client.ClientAuthError as err:
            _LOGGER.debug("ClientAuthError: code=%s, %s", err.code, err.message)
            # Raising ConfigEntryAuthFailed will cancel future updates
            # and start a config flow with SOURCE_REAUTH (async_step_reauth)
            if const.CONF_PASSWORD in self.config_entry.data:
                await self._reauth()
            else:
                raise ConfigEntryAuthFailed from err
        except pesc_client.ClientError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    async def _reauth(self):
        try:
            token = await self.api.async_login(
                self.config_entry.data[const.CONF_USERNAME],
                self.config_entry.data[const.CONF_PASSWORD],
            )
            data = {**self.config_entry.data, const.CONF_TOKEN: token}
            self.hass.config_entries.async_update_entry(self.config_entry, data=data)
            async with async_timeout.timeout(60):
                await self.api.async_fetch_all()
        except pesc_client.ClientAuthError as err:
            raise ConfigEntryAuthFailed from err
        except pesc_client.ClientError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
