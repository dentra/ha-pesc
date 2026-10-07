"""Button implementation routines"""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import const
from .coordinator import PescDataUpdateCoordinator
from .sensor import meter_unique_id, sensor_entity_id
from .services import async_send_linked, failed_results, failure_message


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the platform from config entry."""
    coordinator: PescDataUpdateCoordinator = hass.data[const.DOMAIN][entry.entry_id]
    accounts = {}
    for ind in coordinator.api.meters:
        unique_id = meter_unique_id(ind)
        accounts[unique_id] = accounts[sensor_entity_id(unique_id)] = ind.account
    registry = er.async_get(hass)
    linked = {}
    for link in entry.options.get(const.CONF_LINKS, []):
        # сенсоры могут ещё не попасть в реестр при первой настройке
        reg = registry.async_get(link[ATTR_ENTITY_ID])
        key = reg.unique_id if reg is not None else link[ATTR_ENTITY_ID]
        if account := accounts.get(key):
            linked[account.id] = account

    buttons = [PescSendLinkedButton(entry, account.id) for account in linked.values()]
    unique_ids = {button.unique_id for button in buttons}
    for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg.domain == "button" and reg.unique_id not in unique_ids:
            registry.async_remove(reg.entity_id)

    async_add_entities(buttons)


class PescSendLinkedButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = const.SERVICE_SEND_LINKED
    _attr_icon = "mdi:send"

    def __init__(self, entry: ConfigEntry, account_id: int) -> None:
        self._entry_id = entry.entry_id
        self._account_id = account_id
        self._attr_unique_id = (
            f"{const.DOMAIN}_{account_id}_{const.SERVICE_SEND_LINKED}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(const.DOMAIN, entry.entry_id, account_id)}
        )

    async def async_press(self) -> None:
        results = await async_send_linked(self.hass, self._entry_id, self._account_id)
        if failed := failed_results(results):
            raise HomeAssistantError(failure_message(failed))
