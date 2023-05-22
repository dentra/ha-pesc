"""Sensor implementaion routines"""
import logging
from typing import Callable

import voluptuous as vol

from homeassistant.core import HomeAssistant, callback, HomeAssistantError
from homeassistant.const import UnitOfEnergy
from homeassistant.components import sensor
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import const, pesc_api, pesc_client, PescDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: Callable
):
    """Set up the platform from config entry."""

    _LOGGER.debug("Setup %s (%s)", entry.title, entry.data[const.CONF_USERNAME])

    coordinator: PescDataUpdateCoordinator = hass.data[const.DOMAIN][entry.entry_id]

    diag = entry.options.get(const.CONF_DIAGNOSTIC_SENSORS, False)
    async_add_entities(
        PescSensor(coordinator, entry.entry_id, m, diag) for m in coordinator.api.meters
    )

    if entry.options.get(const.CONF_RATES_SENSORS, False):
        async_add_entities(
            PescRateSensor(coordinator, entry.entry_id, m)
            for m in coordinator.api.meters
        )

    entity_platform.async_get_current_platform().async_register_entity_service(
        name=const.SERVICE_UPDATE_VALUE,
        schema={
            vol.Required(const.CONF_VALUE): vol.All(
                vol.Coerce(int),
                vol.Range(min=1),
            ),
        },
        func=_PescSensor.async_update_value.__name__,
        # required_features=[const.PescEntityFeature.MANUAL],
    )


class _PescSensor(CoordinatorEntity[PescDataUpdateCoordinator], sensor.SensorEntity):
    def __init__(
        self,
        coordinator: PescDataUpdateCoordinator,
        entry_id: str,
        meter: pesc_api.MeterInd,
    ):
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(const.DOMAIN, entry_id)},
            configuration_url=pesc_client.PescClient.BASE_URL,
            name="ПетроЭлектроСбыт",
            model=self.coordinator.api.profile_name,
            # manufacturer="ПетроЭлектроСбыт",
        )
        self.meter = meter

    @property
    def api(self) -> pesc_api.PescApi:
        return self.coordinator.api

    async def async_update_value(self, value: int):
        """nothing to do with RO value"""

    def _update_state_attributes(self):
        pass

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle data update."""
        ind = self.api.find_ind(self.meter.id)
        if ind is not None:
            self.meter = ind
            _LOGGER.debug("[%s] New inndication %s", self.entity_id, self.meter)
            self._update_state_attributes()
        else:
            _LOGGER.warning(
                "[%s] Indication %s not found", self.entity_id, self.meter.id
            )
        super()._handle_coordinator_update()


class PescSensor(_PescSensor):
    def __init__(
        self,
        coordinator: PescDataUpdateCoordinator,
        entry_id: str,
        meter: pesc_api.MeterInd,
        diag: bool = False,
    ):
        super().__init__(coordinator, entry_id, meter)

        self._attr_unique_id = f"pesc_{meter.id}"
        self._attr_device_class = sensor.SensorDeviceClass.ENERGY
        self._attr_state_class = sensor.SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

        self._update_state_attributes()

        if diag:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

        self.entity_id = f"sensor.{self._attr_unique_id}"

    def _update_state_attributes(self):
        self._attr_supported_features = 0
        if not self.meter.auto:
            self._attr_supported_features = const.PescEntityFeature.MANUAL

        self._attr_name = f"{self.meter.account.name} {self.meter.name}"

        self._attr_extra_state_attributes = {
            "date": self.meter.date.isoformat(),
            "name": self.meter.name,
            "scale_id": self.meter.scale_id,
            "meter_id": self.meter.meter.id,
            "serial": self.meter.meter.serial,
            "account_id": self.meter.account.id,
            "address": self.meter.account.address,
            "tenancy": self.meter.account.tenancy,
            "type": self.meter.account.type,
        }

        tariff = self.api.tariff(self.meter.account)
        if tariff is not None:
            self._attr_extra_state_attributes["tariff"] = tariff.name
            rate = tariff.rate(self.meter.scale_id)
            if rate is not None:
                self._attr_extra_state_attributes["tariff_rate"] = rate

    @property
    def native_value(self) -> int:
        """Return the value of the sensor."""
        return self.meter.value

    def __str__(self):
        return f"{self.meter.value}"

    async def async_update_value(self, value: int):
        _LOGGER.debug('[%s]: Updating "%s" to %d', self.entity_id, self.name, value)

        if self.meter.auto:
            raise HomeAssistantError("Показания передаются в автоматическом режиме")

        if value <= self.state:
            raise HomeAssistantError(
                f"Новое значение {value} не больше предыдущего {self.meter.value}"
            )

        await self.api.async_update_value(self.meter, value)

        await self.async_update()


class PescRateSensor(_PescSensor):
    def __init__(
        self,
        coordinator: PescDataUpdateCoordinator,
        entry_id: str,
        meter: pesc_api.MeterInd,
    ):
        super().__init__(coordinator, entry_id, meter)

        self._attr_unique_id = f"pesc_{meter.id}_rate"
        self._attr_native_unit_of_measurement = f"RUB/{UnitOfEnergy.KILO_WATT_HOUR}"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._update_state_attributes()

        self.entity_id = f"sensor.{self._attr_unique_id}"

    def _update_state_attributes(self):
        self._attr_name = f"Тариф {self.meter.account.name} {self.meter.name}"
        tariff = self.coordinator.api.tariff(self.meter.account)
        if tariff is not None:
            self._attr_extra_state_attributes = {
                "tariff": tariff.name,
            }

    @property
    def native_value(self) -> float | None:
        """Return the value of the sensor."""
        tariff = self.coordinator.api.tariff(self.meter.account)
        if tariff is None:
            return None
        return tariff.rate(self.meter.scale_id)
