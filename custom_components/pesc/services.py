"""Service actions"""

import logging

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components import sensor
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_platform

from . import const, pesc_client
from .sensor import PescMeterSensor

_LOGGER = logging.getLogger(__name__)

_SCHEMA_VALUE = vol.All(vol.Coerce(int), vol.Range(min=1))

_UPDATE_VALUE_SCHEMA = vol.All(
    vol.Schema(
        {
            **cv.ENTITY_SERVICE_FIELDS,
            vol.Exclusive(const.CONF_VALUE, const.CONF_VALUE): vol.Any(
                _SCHEMA_VALUE,
                cv.ensure_list(
                    vol.Schema(
                        {
                            vol.Required(const.CONF_SCALE_ID): int,
                            vol.Required(const.CONF_VALUE): _SCHEMA_VALUE,
                        }
                    )
                ),
            ),
            vol.Exclusive(const.CONF_VALUES, const.CONF_VALUE): vol.All(
                cv.ensure_list,
                [
                    vol.Schema(
                        {
                            vol.Required(ATTR_ENTITY_ID): cv.entity_id,
                            vol.Required(const.CONF_VALUE): _SCHEMA_VALUE,
                        }
                    )
                ],
            ),
            vol.Optional("throws", default=True): cv.boolean,
        }
    ),
    cv.has_at_least_one_key(const.CONF_VALUE, const.CONF_VALUES),
)

_TARGET_KEYS = {str(key) for key in cv.ENTITY_SERVICE_FIELDS}

type _Readings = list[tuple[PescMeterSensor, int]]


def async_setup_services(hass: HomeAssistant) -> None:
    """Register service actions."""

    async def async_execute_update_value(service_call: ServiceCall) -> ServiceResponse:
        _LOGGER.debug("async_execute_update_value %s", repr(service_call))

        if const.CONF_VALUES in service_call.data:
            readings = _values_readings(hass, service_call)
        else:
            readings = await _target_readings(hass, service_call)

        results = await _async_send(readings)

        failed = [result for result in results if result["code"] != 0]
        throws = service_call.data["throws"]
        for result in failed if not throws else []:
            _LOGGER.warning("Показания не переданы: %s", result["message"])
        if failed and throws and not service_call.return_response:
            raise HomeAssistantError("; ".join(result["message"] for result in failed))
        if not service_call.return_response:
            return None

        if const.CONF_VALUES not in service_call.data:
            # прежний формат ответа: цели всегда одного счётчика
            results[0].pop("entity_ids")
            return results[0]
        return {
            "code": failed[0]["code"] if failed else 0,
            "message": failed[0]["message"] if failed else const.MESSAGE_SUCCESS,
            "results": results,
        }

    hass.services.async_register(
        const.DOMAIN,
        const.SERVICE_UPDATE_VALUE,
        async_execute_update_value,
        _UPDATE_VALUE_SCHEMA,
        SupportsResponse.OPTIONAL,
    )


def _meter_sensors(hass: HomeAssistant) -> dict[str, PescMeterSensor]:
    return {
        entity.entity_id: entity
        for platform in entity_platform.async_get_platforms(hass, const.DOMAIN)
        if platform.domain == sensor.DOMAIN
        for entity in platform.entities.values()
        if isinstance(entity, PescMeterSensor)
    }


def _values_readings(hass: HomeAssistant, service_call: ServiceCall) -> _Readings:
    if _TARGET_KEYS & service_call.data.keys():
        raise ServiceValidationError("Цели задаются в values, а не в target")

    sensors = _meter_sensors(hass)
    readings: _Readings = []
    for item in service_call.data[const.CONF_VALUES]:
        entity = sensors.get(item[ATTR_ENTITY_ID])
        if entity is None:
            raise ServiceValidationError(
                f"{item[ATTR_ENTITY_ID]} не является сенсором показаний"
            )
        if any(entity is other for other, _ in readings):
            raise ServiceValidationError(f"{entity.entity_id} указан несколько раз")
        readings.append((entity, item[const.CONF_VALUE]))
    return readings


async def _target_readings(hass: HomeAssistant, service_call: ServiceCall) -> _Readings:
    entities = [
        entity
        for platform in entity_platform.async_get_platforms(hass, const.DOMAIN)
        if platform.domain == sensor.DOMAIN
        for entity in await platform.async_extract_from_service(service_call)
    ]
    if not entities:
        raise ServiceValidationError("Ни одной цели не выбрано")
    for entity in entities:
        if not isinstance(entity, PescMeterSensor):
            raise ServiceValidationError("Цель должна быть сенсором показаний")
        if entity.meter.meter.id != entities[0].meter.meter.id:
            raise ServiceValidationError("У всех целей должен быть одинаковый meter_id")

    values = service_call.data[const.CONF_VALUE]
    if not isinstance(values, list):
        # most likely call from gui
        if len(entities) != 1:
            raise ServiceValidationError("Должна быть выбрана только одна цель")
        return [(entities[0], values)]

    if len(entities) != len(values):
        raise ServiceValidationError(
            "Количество целей должно соответствовать количеству сущностей"
        )
    by_scale = {entity.meter.scale_id: entity for entity in entities}
    readings: _Readings = []
    for val in values:
        if (entity := by_scale.get(val[const.CONF_SCALE_ID])) is None:
            raise ServiceValidationError(
                f"Нет цели для scale_id {val[const.CONF_SCALE_ID]}"
            )
        readings.append((entity, val[const.CONF_VALUE]))
    return readings


async def _async_send(readings: _Readings) -> list[dict]:
    """Send readings grouped by meter, one request per meter."""
    groups: dict[tuple, _Readings] = {}
    for entity, value in readings:
        meter = entity.meter
        key = (entity.coordinator, meter.account.id, meter.meter.id)
        groups.setdefault(key, []).append((entity, value))

    results = []
    for group in groups.values():
        errors = [entity.check_value(value) for entity, value in group]
        response = next((error for error in errors if error), None)
        if response is None:
            response = await group[0][0].async_send_values(
                [
                    pesc_client.UpdateValuePayload(
                        scaleId=entity.meter.scale_id, value=value
                    )
                    for entity, value in group
                ]
            )
        results.append(
            {"entity_ids": [entity.entity_id for entity, _ in group]} | response
        )

    # одно обновление на запись после всех отправок
    for coordinator in {key[0] for key in groups}:
        await coordinator.async_request_refresh()
    return results
