import pytest
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.pesc import const

from .conftest import (
    ACCOUNT_ID,
    API_URL,
    PROVIDER_ID,
    calls,
    load_fixture,
    mock_data,
)

SUBSERVICES_URL = f"{API_URL}/v7/accounts/providers/{PROVIDER_ID}/subservices"


async def _setup(hass, aioclient_mock, config_entry) -> None:
    config_entry.add_to_hass(hass)
    mock_data(aioclient_mock)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_meter_sensors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)

    day = hass.states.get("sensor.pesc_00000abc12_2")
    assert float(day.state) == 12345
    assert day.attributes["unit_of_measurement"] == "kWh"
    assert day.attributes["scale_id"] == 2
    assert hass.states.get("sensor.pesc_00000abc12_3_rate") is not None


async def test_valid_entity_id(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)

    assert "invalid entity ID" not in caplog.text


async def test_reading_without_date(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    # счётчик газа из #22
    gas = {
        "id": {"provider": 61, "registration": "2"},
        "name": "Газоснабжение",
        "numberOfDigitsLeft": 6,
        "numberOfDigitsRight": 0,
        "serial": "",
        "status": "ACTIVE",
        "indications": [
            {
                "previousReadingDate": None,
                "meterScaleId": 2,
                "indicationId": None,
                "scaleName": None,
                "previousReading": 6.0,
                "registerReading": None,
                "unit": None,
            }
        ],
        "subserviceId": 4656,
    }
    aioclient_mock.get(
        f"{API_URL}/v6/accounts/{ACCOUNT_ID}/meters/info",
        json=[*load_fixture("meters"), gas],
    )

    await _setup(hass, aioclient_mock, config_entry)

    state = hass.states.get("sensor.pesc_2_2")
    assert float(state.state) == 6
    assert state.attributes["date"] is None
    assert hass.states.get("sensor.pesc_00000abc12_2") is not None


SUBSERVICE_ATTRS = {
    "subservice_id": 54180,
    "subservice_name": "Электроэнергия",
    "subservice_utility": "ELECTRICITY",
}


async def test_subservice_from_meters(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)

    attrs = hass.states.get("sensor.pesc_00000abc12_2").attributes
    assert {key: attrs[key] for key in SUBSERVICE_ATTRS} == SUBSERVICE_ATTRS
    assert not calls(aioclient_mock, "GET", SUBSERVICES_URL)


async def test_subservice_from_catalog(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    meters = load_fixture("meters")
    del meters[0]["subservice"]
    aioclient_mock.get(f"{API_URL}/v6/accounts/{ACCOUNT_ID}/meters/info", json=meters)

    await _setup(hass, aioclient_mock, config_entry)

    attrs = hass.states.get("sensor.pesc_00000abc12_2").attributes
    assert {key: attrs[key] for key in SUBSERVICE_ATTRS} == SUBSERVICE_ATTRS
    assert calls(aioclient_mock, "GET", SUBSERVICES_URL)


async def test_update_value_without_password(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    data = dict(config_entry.data)
    del data[const.CONF_PASSWORD]
    entry = MockConfigEntry(
        domain=const.DOMAIN, version=const.CONFIG_VERSION, data=data
    )
    await _setup(hass, aioclient_mock, entry)
    aioclient_mock.post(
        f"{API_URL}/v7/accounts/{ACCOUNT_ID}/meters/00000ABC12/reading",
        status=401,
        json={"code": "5", "message": "Неавторизованный доступ"},
    )

    response = await hass.services.async_call(
        const.DOMAIN,
        const.SERVICE_UPDATE_VALUE,
        {"entity_id": "sensor.pesc_00000abc12_2", "value": 12346},
        blocking=True,
        return_response=True,
    )
    await hass.async_block_till_done()

    assert response["code"] == "5"
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_update_value_rate_sensor_rejected(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            const.DOMAIN,
            const.SERVICE_UPDATE_VALUE,
            {"entity_id": "sensor.pesc_00000abc12_2_rate", "value": 12346},
            blocking=True,
            return_response=True,
        )
