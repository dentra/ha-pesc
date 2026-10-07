"""Service actions"""

import logging

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components import sensor
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_platform

from . import const, pesc_client
from .sensor import PescMeterSensor

_LOGGER = logging.getLogger(__name__)

_SCHEMA_VALUE = vol.All(vol.Coerce(int), vol.Range(min=1))

_UPDATE_VALUE_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required(const.CONF_VALUE): vol.Any(
            _SCHEMA_VALUE,
            cv.ensure_list(
                vol.Schema(
                    {
                        vol.Required("scale_id"): int,
                        vol.Required(const.CONF_VALUE): _SCHEMA_VALUE,
                    }
                )
            ),
        ),
        vol.Optional("throws", default=True): cv.boolean,
    }
)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register service actions."""

    async def async_execute_update_value(service_call: ServiceCall) -> ServiceResponse:
        # device_id: service_call.data.get(homeassistant.const.ATTR_DEVICE_ID)
        entities = [
            entity
            for platform in entity_platform.async_get_platforms(hass, const.DOMAIN)
            if platform.domain == sensor.DOMAIN
            for entity in await platform.async_extract_from_service(service_call)
        ]

        _LOGGER.debug("async_execute_update_value %s", repr(service_call))

        if not entities:
            raise ServiceValidationError("Ни одной цели не выбрано")

        meter_id = ""
        for entity in entities:
            if not isinstance(entity, PescMeterSensor):
                raise ServiceValidationError("Цель должна быть сенсором показаний")
            if entity.meter.auto:
                raise ServiceValidationError(
                    "Показания цели передаются в автоматическом режиме"
                )
            if not meter_id:
                meter_id = entity.meter.meter.id
            if entity.meter.meter.id != meter_id:
                raise ServiceValidationError(
                    "У всех целей должен быть одинаковый meter_id"
                )

        values = service_call.data[const.CONF_VALUE]
        if not isinstance(values, list):
            # most likely call from gui
            if len(entities) != 1:
                raise ServiceValidationError("Должна быть выбрана только одна цель")
            entity: PescMeterSensor = entities[0]
            values = [{const.CONF_SCALE_ID: entity.meter.scale_id, "value": values}]

        if len(entities) != len(values):
            raise ServiceValidationError(
                "Количество целей должно соответствовать количеству сущностей"
            )

        entity: PescMeterSensor = entities[0]
        throws = service_call.data["throws"]
        response = await entity.async_update_value(
            [
                pesc_client.UpdateValuePayload(
                    scaleId=val[const.CONF_SCALE_ID], value=val[const.CONF_VALUE]
                )
                for val in values
            ],
            service_call.return_response or not throws,
        )
        if not throws and response["code"] != 0:
            _LOGGER.warning("Показания не переданы: %s", response["message"])
        return response if service_call.return_response else None

    hass.services.async_register(
        const.DOMAIN,
        const.SERVICE_UPDATE_VALUE,
        async_execute_update_value,
        _UPDATE_VALUE_SCHEMA,
        SupportsResponse.OPTIONAL,
    )
