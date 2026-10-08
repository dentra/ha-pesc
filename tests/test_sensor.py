import pytest
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)
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
DETAILS_URL = f"{API_URL}/v7/accounts/{ACCOUNT_ID}/details"
METERS_URL = f"{API_URL}/v6/accounts/{ACCOUNT_ID}/meters/info"
RATE = "sensor.pesc_00000abc12_2_rate"


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


@pytest.mark.parametrize("throws", [True, False])
async def test_update_value_throws(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
    throws: bool,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)
    aioclient_mock.post(
        f"{API_URL}/v7/accounts/{ACCOUNT_ID}/meters/00000ABC12/reading",
        status=400,
        json={"code": "42", "message": "Показания не приняты"},
    )

    call = hass.services.async_call(
        const.DOMAIN,
        const.SERVICE_UPDATE_VALUE,
        {"entity_id": "sensor.pesc_00000abc12_2", "value": 12346, "throws": throws},
        blocking=True,
    )
    if throws:
        with pytest.raises(HomeAssistantError, match="Показания не приняты"):
            await call
    else:
        await call
        assert "Показания не приняты" in caplog.text


async def _refresh(hass, aioclient_mock, config_entry, url, **kwargs) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(url, **(kwargs or {"status": 404}))
    mock_data(aioclient_mock)
    await hass.data[const.DOMAIN][config_entry.entry_id].async_refresh()
    await hass.async_block_till_done()


async def test_rate_date(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)

    rate = hass.states.get(RATE)
    assert float(rate.state) == 6.08
    assert rate.attributes["date"]


@pytest.mark.parametrize("url", [DETAILS_URL, METERS_URL])
async def test_rate_kept_on_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
    url: str,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)
    before = hass.states.get(RATE)

    await _refresh(hass, aioclient_mock, config_entry, url)

    if url == DETAILS_URL:
        assert "Failed load tariffs" in caplog.text

    rate = hass.states.get(RATE)
    attrs = dict(rate.attributes)
    # ошибка всего обновления
    assert attrs.pop("assumed_state", False) == (url == METERS_URL)
    assert rate.state == before.state
    assert attrs == before.attributes
    assert float(hass.states.get("sensor.pesc_00000abc12_2").state) == 12345


async def test_rate_restored(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    attrs = {
        "tariff_kind": "Двухтарифный",
        "tariff_rate_name": "День",
        "tariff_rate_detail": "07:00 — 23:00",
        "date": "2026-10-01T10:00:00+03:00",
    }
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(RATE, "6.08", attrs | {"friendly_name": "old"}),
                {"native_value": 6.08, "native_unit_of_measurement": "RUB/kWh"},
            )
        ],
    )
    aioclient_mock.get(DETAILS_URL, status=404)

    await _setup(hass, aioclient_mock, config_entry)

    rate = hass.states.get(RATE)
    assert float(rate.state) == 6.08
    assert {key: rate.attributes.get(key) for key in attrs} == attrs
    assert rate.attributes["friendly_name"] != "old"


async def test_rate_restored_without_subservices(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(RATE, "6.08", {"date": "2026-10-01T10:00:00+03:00"}),
                {"native_value": 6.08, "native_unit_of_measurement": "RUB/kWh"},
            )
        ],
    )
    # подуслуга только в справочнике, а он недоступен
    meters = load_fixture("meters")
    meters[0].pop("subservice")
    aioclient_mock.get(METERS_URL, json=meters)
    aioclient_mock.get(SUBSERVICES_URL, status=404)

    await _setup(hass, aioclient_mock, config_entry)

    rate = hass.states.get(RATE)
    assert float(rate.state) == 6.08
    assert rate.attributes["date"] == "2026-10-01T10:00:00+03:00"


async def test_rate_not_restored_over_fresh(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(RATE, "1.0", {"date": "2026-10-01T10:00:00+03:00"}),
                {"native_value": 1.0, "native_unit_of_measurement": "RUB/kWh"},
            )
        ],
    )

    await _setup(hass, aioclient_mock, config_entry)

    rate = hass.states.get(RATE)
    assert float(rate.state) == 6.08
    assert rate.attributes["date"] != "2026-10-01T10:00:00+03:00"


async def test_rate_cleared_without_tariff(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)

    await _refresh(hass, aioclient_mock, config_entry, DETAILS_URL, json=[])

    rate = hass.states.get(RATE)
    assert rate.state == "unknown"
    assert "date" not in rate.attributes
    assert "tariff_kind" not in rate.attributes


async def test_rate_restored_dropped(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(RATE, "6.08", {"date": "2026-10-01T10:00:00+03:00"}),
                {"native_value": 6.08, "native_unit_of_measurement": "RUB/kWh"},
            )
        ],
    )
    aioclient_mock.get(DETAILS_URL, status=404)
    await _setup(hass, aioclient_mock, config_entry)
    assert float(hass.states.get(RATE).state) == 6.08

    await _refresh(hass, aioclient_mock, config_entry, DETAILS_URL, json=[])

    rate = hass.states.get(RATE)
    assert rate.state == "unknown"
    assert "date" not in rate.attributes


async def test_rate_not_restored_when_loaded(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(RATE, "6.08", {"date": "2026-10-01T10:00:00+03:00"}),
                {"native_value": 6.08, "native_unit_of_measurement": "RUB/kWh"},
            )
        ],
    )
    aioclient_mock.get(DETAILS_URL, json=[])

    await _setup(hass, aioclient_mock, config_entry)

    rate = hass.states.get(RATE)
    assert rate.state == "unknown"
    assert "date" not in rate.attributes
