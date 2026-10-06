import logging

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.pesc import const

from .conftest import API_URL, AUTH, AUTH_URL, mock_2fa_required, mock_data


async def test_setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    mock_data(aioclient_mock)

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED


async def test_expired_verified_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.pesc")
    config_entry.add_to_hass(hass)
    aioclient_mock.get(
        f"{API_URL}/v6/users/current",
        status=401,
        json={"code": "5", "message": "Неавторизованный доступ"},
    )
    mock_2fa_required(aioclient_mock, ["EMAIL", "PHONE"])

    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
    assert "Требуется подтверждение вторым фактором, код 424" in caplog.text


async def test_relogin_keeps_entry_loaded(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    setup_calls: list,
) -> None:
    config_entry.add_to_hass(hass)
    mock_data(aioclient_mock)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    aioclient_mock.post(AUTH_URL, json={"auth": "auth-2", "access": "access-2"})

    await hass.data[const.DOMAIN][config_entry.entry_id]._relogin(True)
    await hass.async_block_till_done()

    assert len(setup_calls) == 1
    assert config_entry.data[const.CONF_AUTH] == {
        "auth": "auth-2",
        "access": "access-2",
        "verified": AUTH["verified"],
    }


async def test_options_change_reloads(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    setup_calls: list,
) -> None:
    config_entry.add_to_hass(hass)
    mock_data(aioclient_mock)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    hass.config_entries.async_update_entry(
        config_entry, options={const.CONF_RATES_SENSORS: False}
    )
    await hass.async_block_till_done()

    assert len(setup_calls) == 2


async def test_service_without_entries(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, const.DOMAIN, {})

    assert hass.services.has_service(const.DOMAIN, const.SERVICE_UPDATE_VALUE)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            const.DOMAIN,
            const.SERVICE_UPDATE_VALUE,
            {"entity_id": "sensor.pesc_00000abc12_2", "value": 1},
            blocking=True,
        )
