import logging

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.pesc import const

from .conftest import (
    API_URL,
    AUTH,
    AUTH_URL,
    BASE_URL,
    TRANSACTION_ID,
    calls,
    mock_2fa_required,
    mock_data,
)

SITE_CONFIG = {
    "users": {
        "authentication": {
            "type": {"available": ["EMAIL", "PHONE"], "default": "PHONE"}
        }
    }
}

NEW_AUTH = {"auth": "auth-2", "access": "access-2", "verified": "verified-2"}


async def _start_user_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f"{BASE_URL}/config.json", json=SITE_CONFIG)
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"login_type": "phone"}
    )
    assert result["step_id"] == "auth"

    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"phone": "+70000000000", "password": "secret"}
    )


async def test_user_flow_sms(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_2fa_required(aioclient_mock, ["EMAIL", "PHONE", "FLASHCALL"])
    mock_data(aioclient_mock)
    aioclient_mock.post(
        f"{API_URL}/v7/users/{TRANSACTION_ID}/phone/check/confirmation/send",
        text="+7 (***) ***-00-00",
    )
    aioclient_mock.post(
        f"{API_URL}/v7/users/{TRANSACTION_ID}/phone/check/verification", json=NEW_AUTH
    )

    result = await _start_user_flow(hass, aioclient_mock)
    assert result["step_id"] == "send_code"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"verify_type": "PHONE"}
    )
    assert result["step_id"] == "verify_code"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"verify_code": "12345"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][const.CONF_AUTH] == NEW_AUTH
    assert result["data"][const.CONF_PASSWORD] == "secret"

    headers = calls(aioclient_mock, "POST", AUTH_URL)[0][3]
    assert headers["withTotp"] == "true"


async def test_user_flow_totp(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_2fa_required(aioclient_mock, ["EMAIL", "PHONE", "FLASHCALL", "TOTP"])
    mock_data(aioclient_mock)
    aioclient_mock.post(f"{API_URL}/v1/dfa/{TRANSACTION_ID}/totp/verify", json=NEW_AUTH)

    result = await _start_user_flow(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"verify_type": "TOTP"}
    )
    assert result["step_id"] == "totp_code"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"totp_code": "12"}
    )
    assert result["errors"] == {"totp_code": "invalid_totp"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"totp_code": "123456"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][const.CONF_AUTH] == NEW_AUTH


async def test_user_flow_unsupported_types(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_2fa_required(aioclient_mock, ["PIGEON"])

    result = await _start_user_flow(hass, aioclient_mock)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_verify_types"


async def test_reauth_relogin_keeps_verified(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.pesc")
    config_entry.add_to_hass(hass)
    mock_data(aioclient_mock)
    aioclient_mock.post(AUTH_URL, json={"auth": "auth-2", "access": "access-2"})

    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "new-secret"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[const.CONF_AUTH] == {
        "auth": "auth-2",
        "access": "access-2",
        "verified": AUTH["verified"],
    }
    assert config_entry.data[const.CONF_PASSWORD] == "new-secret"
    assert AUTH["verified"] not in caplog.text
    assert "new-secret" not in caplog.text
    assert "auth-2" not in caplog.text

    headers = calls(aioclient_mock, "POST", AUTH_URL)[0][3]
    assert headers["Auth-verification"] == AUTH["verified"]


async def test_reauth_second_factor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    mock_data(aioclient_mock)
    mock_2fa_required(aioclient_mock, ["EMAIL", "PHONE", "FLASHCALL"])
    aioclient_mock.post(
        f"{API_URL}/v7/users/{TRANSACTION_ID}/phone/check/confirmation/send",
        text="+7 (***) ***-00-00",
    )
    aioclient_mock.post(
        f"{API_URL}/v7/users/{TRANSACTION_ID}/phone/check/verification", json=NEW_AUTH
    )

    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "secret"}
    )
    assert result["step_id"] == "send_code"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"verify_type": "PHONE"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"verify_code": "12345"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[const.CONF_AUTH] == NEW_AUTH
