"""Sensor implementaion routines"""
import logging
from typing import Callable

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components import sensor
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import (
    HomeAssistant,
    HomeAssistantError,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import PescDataUpdateCoordinator, const, pesc_api, pesc_client

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: Callable
):
    """Set up the platform from config entry."""

    _LOGGER.debug("Setup %s (%s)", entry.title, entry.data[const.CONF_USERNAME])

    coordinator: PescDataUpdateCoordinator = hass.data[const.DOMAIN][entry.entry_id]

    diag = entry.options.get(const.CONF_DIAGNOSTIC_SENSORS, False)
    async_add_entities(
        PescMeterSensor(coordinator, m, diag) for m in coordinator.api.meters
    )

    if entry.options.get(const.CONF_RATES_SENSORS, True):
        async_add_entities(
            PescRateSensor(coordinator, m) for m in coordinator.api.meters
        )

    service_schema = {
        vol.Required(const.CONF_VALUE): vol.All(
            vol.Coerce(int),
            vol.Range(min=1),
        ),
        vol.Optional("throws"): vol.All(
            vol.Coerce(bool),
            vol.DefaultTo(True),
        ),
    }

    platform = entity_platform.async_get_current_platform()

    async def async_execute_update_valaue(service_call: ServiceCall) -> ServiceResponse:
        # device_id: service_call.data.get(homeassistant.const.ATTR_DEVICE_ID)
        entities = await platform.async_extract_from_service(service_call)
        if len(entities) != 1:
            raise HomeAssistantError("Only one entity should be selected")

        entity = entities[0]
        if not isinstance(entity, _PescMeterSensor):
            raise HomeAssistantError("PescMeterSensor entity should be selected")

        return await entity.async_update_value(
            service_call.data[const.CONF_VALUE],
            service_call.return_response,
        )

    hass.services.async_register(
        const.DOMAIN,
        const.SERVICE_UPDATE_VALUE,
        async_execute_update_valaue,
        cv.make_entity_service_schema(service_schema),
        SupportsResponse.OPTIONAL,
    )


class _PescBaseSensor(
    CoordinatorEntity[PescDataUpdateCoordinator], sensor.SensorEntity
):
    def __init__(
        self,
        coordinator: PescDataUpdateCoordinator,
        account_id: int,
        unique_id: str,
        name: str,
        model: str,
    ):
        super().__init__(coordinator)

        entry = coordinator.config_entry

        _LOGGER.debug("Initialize %s for %s", self.__class__.__name__, entry.title)

        self._attr_unique_id = unique_id

        self._attr_device_info = DeviceInfo(
            configuration_url=pesc_client.PescClient.BASE_URL,
            # connections={},
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(const.DOMAIN, entry.entry_id, account_id)},
            manufacturer=self.coordinator.api.profile_name,
            model=model,
            name=name,
            # sw_version="",
            # hw_version="",
        )

        self.entity_id = f"sensor.{self._attr_unique_id}"

    @property
    def api(self) -> pesc_api.PescApi:
        return self.coordinator.api


class _PescMeterSensor(_PescBaseSensor):
    def __init__(
        self,
        coordinator: PescDataUpdateCoordinator,
        meter: pesc_api.MeterInd,
        id_suffix: str = "",
    ):
        super().__init__(
            coordinator,
            meter.account.id,
            f"{const.DOMAIN}_{meter.id}{id_suffix}",
            meter.account.name,
            meter.account.tenancy,
        )
        self.meter = meter
        self._update_state_attributes()

    async def async_update_value(
        self, value: int, return_response: bool = True
    ) -> ServiceResponse:
        """nothing to do with RO value"""

    def _update_state_attributes(self):
        pass

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle data update."""
        ind = self.api.find_ind(self.meter.id)
        if ind is not None:
            self.meter = ind
            _LOGGER.debug("[%s] New indication %s", self.entity_id, self.meter)
            self._update_state_attributes()
        else:
            _LOGGER.warning(
                "[%s] Indication %s not found", self.entity_id, self.meter.id
            )
        super()._handle_coordinator_update()

    @property
    def assumed_state(self) -> bool:
        return not self.coordinator.last_update_success

    @property
    def available(self) -> bool:
        return True


class PescMeterSensor(_PescMeterSensor):
    _attr_state_class = sensor.SensorStateClass.TOTAL_INCREASING
    _attr_has_entity_name = True
    _attr_supported_features = 0
    _attr_translation_key = "meter"

    def __init__(
        self,
        coordinator: PescDataUpdateCoordinator,
        meter: pesc_api.MeterInd,
        diag: bool = False,
    ):
        super().__init__(coordinator, meter)
        if diag:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    def _update_state_attributes(self):
        if not self.meter.auto:
            self._attr_supported_features = const.PescEntityFeature.MANUAL

        subservice = self.api.subservice(self.meter.meter.subservice_id)
        utility = "" if subservice is None else subservice["utility"]
        if utility == pesc_client.SubserviceUtility.ELECTRICITY:
            self._attr_device_class = sensor.SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        elif utility == pesc_client.SubserviceUtility.GAS:
            self._attr_device_class = sensor.SensorDeviceClass.GAS
            self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
        elif utility == pesc_client.SubserviceUtility.WATER:
            self._attr_device_class = sensor.SensorDeviceClass.WATER
            self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
        # TODO implement HEATING device_class and unit_of_measurement
        # elif utility == pesc_client.SubserviceUtility.HEATING:
        else:
            self._attr_native_unit_of_measurement = self.meter.unit

        self._attr_name = self.meter.name
        self._attr_extra_state_attributes = {
            "type": self.meter.account.type,
            "date": self.meter.date.isoformat(),
            "name": self.meter.name,
            "scale_id": self.meter.scale_id,
            "meter_id": self.meter.meter.id,
            "serial": self.meter.meter.serial,
            "account_id": str(self.meter.account.id),
            "tenancy": self.meter.account.tenancy,
            "address": self.meter.account.address,
        }

        if subservice is not None:
            self._attr_extra_state_attributes["subservice_id"] = subservice["id"]
            self._attr_extra_state_attributes["subservice_name"] = subservice["name"]
            self._attr_extra_state_attributes["subservice_type"] = subservice["type"]
            self._attr_extra_state_attributes["subservice_utility"] = subservice[
                "utility"
            ]

        tariff = self.api.tariff(self.meter)
        if tariff is not None:
            if tariff.name is not None:
                self._attr_extra_state_attributes["tariff_kind"] = tariff.kind
            rate = tariff.rate(self.meter.scale_id)
            if rate is not None:
                self._attr_extra_state_attributes["tariff_rate"] = rate

    @property
    def native_value(self) -> int:
        """Return the value of the sensor."""
        return self.meter.value

    def __str__(self):
        return f"{self.meter.value}"

    async def async_update_value(
        self, value: int, return_response: bool = True
    ) -> ServiceResponse:
        _LOGGER.debug('[%s]: Updating "%s" to %d', self.entity_id, self.name, value)

        if self.meter.auto:
            msg = "Показания передаются в автоматическом режиме"
            if not return_response:
                raise HomeAssistantError(msg)
            return {"code": -2, "message": msg}

        if value < self.state:
            msg = f"Новое значение {value} меньше предыдущего {int(self.meter.value)}"
            if not return_response:
                raise HomeAssistantError(msg)
            return {"code": -3, "message": msg}

        res = await self.relogin_and_update_(value, return_response, False)
        await self.async_update()
        return res

    async def relogin_and_update_(
        self, value: int, return_response: bool, do_relogin: bool
    ) -> ServiceResponse:
        try:
            if do_relogin:
                await self.coordinator.relogin()
            payload = await self.api.async_update_value(self.meter, value)
            _LOGGER.debug('[%s] Update "%s" success', self.entity_id, self.name)
            return {
                "code": 0,
                "message": "Операция выполнена успешно",
                "payload": payload,
            }
        except pesc_client.ClientAuthError as err:
            if not do_relogin:
                await self.relogin_and_update_(value, return_response, True)
                return None
            if not return_response:
                raise ConfigEntryAuthFailed from err
            return {"code": err.code, "message": err.message}
        except pesc_client.ClientError as err:
            if not return_response:
                raise HomeAssistantError(f"Ошибка вызова API: {err}") from err
            return {"code": err.code, "message": err.message}


class PescRateSensor(_PescMeterSensor):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "meter"
    _attr_icon = "mdi:currency-rub"

    def __init__(
        self,
        coordinator: PescDataUpdateCoordinator,
        meter: pesc_api.MeterInd,
    ):
        super().__init__(coordinator, meter, "_rate")

        subservice = self.api.subservice(self.meter.meter.subservice_id)
        utility = "" if subservice is None else subservice["utility"]
        if utility == pesc_client.SubserviceUtility.ELECTRICITY:
            self._attr_native_unit_of_measurement = (
                f"{const.CURRENCY_RUB}/{UnitOfEnergy.KILO_WATT_HOUR}"
            )
        elif utility == pesc_client.SubserviceUtility.GAS:
            self._attr_native_unit_of_measurement = (
                f"{const.CURRENCY_RUB}/{UnitOfVolume.CUBIC_METERS}"
            )
        elif utility == pesc_client.SubserviceUtility.WATER:
            self._attr_native_unit_of_measurement = (
                f"{const.CURRENCY_RUB}/{UnitOfVolume.CUBIC_METERS}"
            )
        # TODO implement HEATING device_class and unit_of_measurement
        # elif utility == pesc_client.SubserviceUtility.HEATING:
        else:
            self._attr_native_unit_of_measurement = (
                f"{const.CURRENCY_RUB}/{self.meter.unit}"
            )

    def _update_state_attributes(self):
        self._attr_name = f"Тариф {self.meter.name}"
        tariff = self.coordinator.api.tariff(self.meter)
        if tariff is not None:
            self._attr_extra_state_attributes = {
                "tariff_kind": tariff.kind,
            }

    @property
    def native_value(self) -> float | None:
        """Return the value of the sensor."""
        tariff = self.coordinator.api.tariff(self.meter)
        if tariff is None:
            return None
        return tariff.rate(self.meter.scale_id)
