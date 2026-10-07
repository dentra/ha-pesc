import logging
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import const, pesc_client
from .coordinator import PescDataUpdateCoordinator
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)


PLATFORMS: Final = ["sensor"]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(const.DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""

    _LOGGER.debug("Setup %s (%s)", entry.title, entry.data[const.CONF_USERNAME])

    hass.data.setdefault(const.DOMAIN, {})

    coordinator = PescDataUpdateCoordinator(hass, entry)
    hass.data[const.DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unload %s (%s)", entry.title, entry.data[const.CONF_USERNAME])
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[const.DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry."""

    _LOGGER.debug("Migrating configuration from version %s", config_entry.version)

    if config_entry.version == 1:
        new_data = {
            **config_entry.data,
            const.CONF_AUTH: {pesc_client.AUTH_AUTH: config_entry.data.get("token")},
            const.CONF_LOGIN_TYPE: pesc_client.LOGIN_TYPE_PHONE,
        }

        hass.config_entries.async_update_entry(
            config_entry, data=new_data, version=const.CONFIG_VERSION
        )

    _LOGGER.debug(
        "Migration to configuration version %s successful", config_entry.version
    )

    return True
