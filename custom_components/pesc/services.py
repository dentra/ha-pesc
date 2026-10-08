"""Service actions"""

import logging
import math

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components import sensor
from homeassistant.const import ATTR_ENTITY_ID, ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.util.unit_conversion import EnergyConverter, VolumeConverter

from . import const, pesc_client
from .sensor import PescMeterSensor

_LOGGER = logging.getLogger(__name__)

_SCHEMA_VALUE = vol.All(vol.Coerce(int), vol.Range(min=1))
_THROWS = {vol.Optional("throws", default=True): cv.boolean}

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
            **_THROWS,
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
        response = _response(results, service_call)
        if response is not None and const.CONF_VALUES not in service_call.data:
            # прежний формат ответа: цели всегда одного счётчика
            results[0].pop("entity_ids")
            return results[0]
        return response

    async def async_execute_send_linked(service_call: ServiceCall) -> ServiceResponse:
        results = await async_send_linked(hass)
        return _response(results, service_call)

    hass.services.async_register(
        const.DOMAIN,
        const.SERVICE_UPDATE_VALUE,
        async_execute_update_value,
        _UPDATE_VALUE_SCHEMA,
        SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        const.DOMAIN,
        const.SERVICE_SEND_LINKED,
        async_execute_send_linked,
        vol.Schema(_THROWS),
        SupportsResponse.OPTIONAL,
    )


def _response(results: list[dict], service_call: ServiceCall) -> ServiceResponse:
    failed = failed_results(results)
    throws = service_call.data["throws"]
    if not throws:
        for result in failed:
            _LOGGER.warning("Показания не переданы: %s", result["message"])
    if failed and throws and not service_call.return_response:
        raise HomeAssistantError(failure_message(failed))
    if not service_call.return_response:
        return None
    return {
        "code": failed[0]["code"] if failed else 0,
        "message": failed[0]["message"] if failed else const.MESSAGE_SUCCESS,
        "results": results,
    }


def failed_results(results: list[dict]) -> list[dict]:
    return [result for result in results if result["code"] != 0]


def failure_message(failed: list[dict]) -> str:
    return "; ".join(result["message"] for result in failed)


async def async_send_linked(
    hass: HomeAssistant, entry_id: str | None = None, account_id: int | None = None
) -> list[dict]:
    """Send readings of linked source sensors."""
    sensors = _meter_sensors(hass)
    readings: _Readings = []
    results = []
    for entry in hass.config_entries.async_loaded_entries(const.DOMAIN):
        if entry_id is not None and entry.entry_id != entry_id:
            continue
        for link in entry.options.get(const.CONF_LINKS, []):
            entity = sensors.get(link[ATTR_ENTITY_ID])
            if entity is None:
                # у кнопки свой счёт, без счётчика его не определить
                if account_id is None:
                    results.append(_missing_meter(link))
                continue
            if account_id is not None and entity.meter.account.id != account_id:
                continue
            try:
                readings.append((entity, _source_value(hass, entity, link)))
            except _SourceError as err:
                results.append({"entity_ids": [entity.entity_id], **err.response})

    if not readings and not results:
        raise ServiceValidationError("Нет связанных сенсоров")
    return results + await _async_send(readings)


def linked_preview(hass: HomeAssistant, entry_id: str) -> str:
    """Describe what send_linked would send for the entry."""
    entry = hass.config_entries.async_get_entry(entry_id)
    sensors = _meter_sensors(hass)
    lines = []
    for link in entry.options.get(const.CONF_LINKS, []) if entry else []:
        entity = sensors.get(link[ATTR_ENTITY_ID])
        if entity is None:
            lines.append(
                f"- **{link[ATTR_ENTITY_ID]}**: {_missing_meter(link)['message']}"
            )
            continue
        source = hass.states.get(link[const.CONF_SOURCE])
        source_name = source.name if source else link[const.CONF_SOURCE]
        try:
            value = _source_value(hass, entity, link)
            if error := entity.check_value(value):
                text = error["message"]
            else:
                text = f"{value} (сейчас {_number(entity.meter.value)})"
        except _SourceError as err:
            text = err.response["message"]
        name = entity.name if isinstance(entity.name, str) else entity.entity_id
        lines.append(f"- **{name}**: {text} ← {source_name}")
    return "\n".join(["Сейчас будет передано:", *lines]) if lines else ""


def _missing_meter(link: dict) -> dict:
    message = f"Счетчик {link[ATTR_ENTITY_ID]} не найден"
    return {"entity_ids": [link[ATTR_ENTITY_ID]], "code": -6, "message": message}


def _number(value: float | None) -> str:
    if value is None:
        return "нет"
    return str(int(value)) if float(value).is_integer() else str(value)


class _SourceError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.response = {"code": code, "message": message}


def _source_value(hass: HomeAssistant, entity: PescMeterSensor, link: dict) -> int:
    source = link[const.CONF_SOURCE]
    state = hass.states.get(source)
    try:
        value = float(state.state) if state is not None else None
    except ValueError:
        value = None
    if value is None:
        raise _SourceError(-4, f"Источник {source} недоступен")
    if not math.isfinite(value):
        raise _SourceError(
            -4, f"Источник {source}: некорректное значение {state.state}"
        )

    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    target = entity.native_unit_of_measurement
    if unit and target and unit != target:
        converter = next(
            (
                conv
                for conv in (EnergyConverter, VolumeConverter)
                if unit in conv.VALID_UNITS and target in conv.VALID_UNITS
            ),
            None,
        )
        if converter is None:
            msg = f"Единица {unit} источника {source} не подходит к {target}"
            raise _SourceError(-5, msg)
        value = converter.convert(value, unit, target)
    # погрешность пересчёта не должна отнимать единицу
    reading = math.floor(round(value, 6))
    if reading < 1:
        raise _SourceError(
            -4, f"Источник {source}: некорректное значение {state.state}"
        )
    return reading


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
        if (entity := by_scale.pop(val[const.CONF_SCALE_ID], None)) is None:
            raise ServiceValidationError(
                f"Нет цели для scale_id {val[const.CONF_SCALE_ID]} или он указан несколько раз"
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
