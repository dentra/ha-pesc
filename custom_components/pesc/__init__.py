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

from . import pesc_api
from . import pesc_client

from .const import (
    DOMAIN,
    CONF_TOKEN,
    CONF_USERNAME,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


PLATFORMS: Final = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""

    _LOGGER.debug("Setup %s (%s)", entry.title, entry.data[CONF_USERNAME])

    hass.data.setdefault(DOMAIN, {})

    coordinator = PescDataUpdateCoordinator(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = coordinator
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
    _LOGGER.debug("Unload %s (%s)", entry.title, entry.data[CONF_USERNAME])
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


# https://developers.home-assistant.io/docs/integration_fetching_data/#polling-api-endpoints
class PescDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_UPDATE_INTERVAL
        )

        self.api = pesc_api.PescApi(
            pesc_client.PescClient(
                async_get_clientsession(hass), entry.data[CONF_TOKEN]
            )
        )

        if CONF_UPDATE_INTERVAL in entry.options:
            self.update_interval = cv.time_period(entry.options[CONF_UPDATE_INTERVAL])

    async def _async_update_data(self):
        # FIXME remove testing
        # if self.update_interval.total_seconds() == 11:
        #     raise ConfigEntryAuthFailed()

        try:
            # asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with async_timeout.timeout(10):
                return await self.api.async_fetch_all()
        except pesc_client.ClientAuthError as err:
            # Raising ConfigEntryAuthFailed will cancel future updates
            # and start a config flow with SOURCE_REAUTH (async_step_reauth)
            raise ConfigEntryAuthFailed from err
        except pesc_client.ClientError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
