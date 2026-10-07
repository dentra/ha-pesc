import copy

import aiohttp

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.pesc import const

from .conftest import ACCOUNT_ID, API_URL, load_fixture, mock_data

METER_A = "00000ABC12"
METER_B = "00000DEF56"
DAY_A = "sensor.pesc_00000abc12_2"
NIGHT_A = "sensor.pesc_00000abc12_3"
DAY_B = "sensor.pesc_00000def56_2"


def _reading_url(meter: str) -> str:
    return f"{API_URL}/v7/accounts/{ACCOUNT_ID}/meters/{meter}/reading"


@pytest.fixture
async def two_meters(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    meters = load_fixture("meters")
    meter_b = copy.deepcopy(meters[0])
    meter_b["id"]["registration"] = METER_B
    aioclient_mock.get(
        f"{API_URL}/v6/accounts/{ACCOUNT_ID}/meters/info", json=[*meters, meter_b]
    )
    mock_data(aioclient_mock)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def _call(hass: HomeAssistant, data: dict, **kwargs):
    return await hass.services.async_call(
        const.DOMAIN,
        const.SERVICE_UPDATE_VALUE,
        data,
        blocking=True,
        return_response=kwargs.pop("return_response", True),
        **kwargs,
    )


def _sent(aioclient_mock: AiohttpClientMocker, meter: str) -> list:
    return [
        call[2]
        for call in aioclient_mock.mock_calls
        if call[0].upper() == "POST" and str(call[1]) == _reading_url(meter)
    ]


@pytest.mark.usefixtures("two_meters")
async def test_values_several_meters(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(_reading_url(METER_A))
    aioclient_mock.post(_reading_url(METER_B))

    response = await _call(
        hass,
        {
            "values": [
                {"entity_id": DAY_A, "value": 12346},
                {"entity_id": NIGHT_A, "value": 6790},
                {"entity_id": DAY_B, "value": 12347},
            ]
        },
    )

    assert response["code"] == 0
    assert [result["code"] for result in response["results"]] == [0, 0]
    assert _sent(aioclient_mock, METER_A) == [
        [{"scaleId": 2, "value": 12346}, {"scaleId": 3, "value": 6790}]
    ]
    assert _sent(aioclient_mock, METER_B) == [
        [{"scaleId": 2, "value": 12347}, {"scaleId": 3, "value": 6789.0}]
    ]


@pytest.mark.usefixtures("two_meters")
async def test_values_api_error_sends_others(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(
        _reading_url(METER_A),
        status=400,
        json={"code": "42", "message": "Показания не приняты"},
    )
    aioclient_mock.post(_reading_url(METER_B))

    response = await _call(
        hass,
        {
            "values": [
                {"entity_id": DAY_A, "value": 12346},
                {"entity_id": DAY_B, "value": 12347},
            ]
        },
    )

    assert response["code"] == "42"
    assert response["message"] == "Показания не приняты"
    assert [result["code"] for result in response["results"]] == ["42", 0]
    assert _sent(aioclient_mock, METER_B)


@pytest.mark.usefixtures("two_meters")
async def test_values_lower_value_skips_meter(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(_reading_url(METER_A))
    aioclient_mock.post(_reading_url(METER_B))

    response = await _call(
        hass,
        {
            "values": [
                {"entity_id": DAY_A, "value": 12346},
                {"entity_id": DAY_B, "value": 100},
            ]
        },
    )

    assert response["code"] == -3
    assert [result["code"] for result in response["results"]] == [0, -3]
    assert response["results"][1]["entity_ids"] == [DAY_B]
    assert _sent(aioclient_mock, METER_A)
    assert not _sent(aioclient_mock, METER_B)


@pytest.mark.usefixtures("two_meters")
async def test_values_single_meter_results(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(_reading_url(METER_A))

    response = await _call(hass, {"values": [{"entity_id": DAY_A, "value": 12346}]})

    assert response == {
        "code": 0,
        "message": "Операция выполнена успешно",
        "results": [
            {
                "entity_ids": [DAY_A],
                "code": 0,
                "message": "Операция выполнена успешно",
                "payload": [
                    {"scaleId": 2, "value": 12346},
                    {"scaleId": 3, "value": 6789.0},
                ],
            }
        ],
    }


@pytest.mark.usefixtures("two_meters")
@pytest.mark.parametrize(
    "data",
    [
        {"value": 12346, "values": [{"entity_id": DAY_A, "value": 12346}]},
        {},
        {"entity_id": DAY_A, "values": [{"entity_id": DAY_A, "value": 12346}]},
        {"values": [{"entity_id": "sensor.unknown", "value": 12346}]},
        {"values": [{"entity_id": f"{DAY_A}_rate", "value": 12346}]},
        {
            "values": [
                {"entity_id": DAY_A, "value": 12346},
                {"entity_id": DAY_A, "value": 12347},
            ]
        },
        {"entity_id": [DAY_A, NIGHT_A], "value": [{"scale_id": 9, "value": 1}]},
    ],
    ids=[
        "value_and_values",
        "nothing",
        "values_and_target",
        "unknown",
        "rate",
        "duplicate",
        "unknown_scale",
    ],
)
async def test_values_invalid_call(hass: HomeAssistant, data: dict) -> None:
    with pytest.raises((ServiceValidationError, vol.Invalid)):
        await _call(hass, data)


@pytest.mark.usefixtures("two_meters")
@pytest.mark.parametrize("throws", [True, False])
async def test_values_throws(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
    throws: bool,
) -> None:
    aioclient_mock.post(_reading_url(METER_A))
    data = {
        "values": [
            {"entity_id": DAY_A, "value": 12346},
            {"entity_id": DAY_B, "value": 100},
        ],
        "throws": throws,
    }

    if throws:
        with pytest.raises(HomeAssistantError, match="меньше предыдущего"):
            await _call(hass, data, return_response=False)
    else:
        assert await _call(hass, data, return_response=False) is None
        assert "меньше предыдущего" in caplog.text
    assert _sent(aioclient_mock, METER_A)


@pytest.mark.usefixtures("two_meters")
async def test_values_network_error_sends_others(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(_reading_url(METER_A), exc=aiohttp.ClientConnectionError())
    aioclient_mock.post(_reading_url(METER_B))

    response = await _call(
        hass,
        {
            "values": [
                {"entity_id": DAY_A, "value": 12346},
                {"entity_id": DAY_B, "value": 12347},
            ]
        },
    )

    assert [result["code"] for result in response["results"]] == [-1, 0]
    assert response["code"] == -1
    assert _sent(aioclient_mock, METER_B)


@pytest.mark.usefixtures("two_meters")
async def test_target_list_checks_all_scales(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(_reading_url(METER_A))

    response = await _call(
        hass,
        {
            "entity_id": [DAY_A, NIGHT_A],
            "value": [{"scale_id": 2, "value": 12346}, {"scale_id": 3, "value": 1}],
        },
    )

    assert response["code"] == -3
    assert "results" not in response
    assert not _sent(aioclient_mock, METER_A)


async def test_values_meter_without_reading(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    meters = load_fixture("meters")
    meters[0]["indications"][0]["previousReading"] = None
    aioclient_mock.get(f"{API_URL}/v6/accounts/{ACCOUNT_ID}/meters/info", json=meters)
    mock_data(aioclient_mock)
    aioclient_mock.post(_reading_url(METER_A))
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    response = await _call(hass, {"values": [{"entity_id": DAY_A, "value": 1}]})

    assert response["code"] == 0
