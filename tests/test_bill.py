import html
import re
from http import HTTPStatus

import pytest
import aiohttp
from homeassistant.components import persistent_notification
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.pesc import const

from .conftest import ACCOUNT_ID, API_URL, AUTH_URL, calls, load_fixture, mock_data

UNIQUE_ID = f"pesc_{ACCOUNT_ID}_bill"
BILL_ID = "260900000100001"
BILL_URL = f"{API_URL}/v8/accounts/{ACCOUNT_ID}/payments/bills/current"
UUID_URL = f"{API_URL}/v7/accounts/{ACCOUNT_ID}/payments/bills/{BILL_ID}/uuid"
FILE_UUID = "efe081e0-144d-4978-8879-66c700697f56"
FILE_URL = f"{API_URL}/v1/file/{FILE_UUID}"
PDF = b"%PDF-1.4 bill"


async def _setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    bill: dict | None = None,
) -> None:
    assert await async_setup_component(hass, "http", {})
    config_entry.add_to_hass(hass)
    mock_data(aioclient_mock)
    aioclient_mock.get(BILL_URL, json=bill or load_fixture("bill"))
    # ПЕСК отдаёт uuid без кавычек, но с типом application/json
    aioclient_mock.get(
        UUID_URL, text=FILE_UUID, headers={"content-type": "application/json"}
    )
    aioclient_mock.get(
        FILE_URL, content=PDF, headers={"content-type": "application/pdf"}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


def _button(hass: HomeAssistant) -> str | None:
    return er.async_get(hass).async_get_entity_id("button", const.DOMAIN, UNIQUE_ID)


async def _press(hass: HomeAssistant) -> str:
    await hass.services.async_call(
        "button", "press", {"entity_id": _button(hass)}, blocking=True
    )
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    return notifications[f"pesc_bill_{ACCOUNT_ID}"]["message"]


def _link(message: str) -> str:
    match = re.search(r'<a href="([^"]+)" target="_blank">', message)
    assert match is not None, message
    return html.unescape(match.group(1))


async def test_button_created(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)

    entry = er.async_get(hass).async_get(_button(hass))
    assert entry is not None
    assert entry.translation_key == "bill"
    # квитанция запрашивается только по нажатию
    assert not calls(aioclient_mock, "GET", BILL_URL)


async def test_button_press(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    hass_client_no_auth,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)

    message = await _press(hass)

    assert "Квитанция за сентябрь 2026" in message
    assert "2375,69 ₽" in message
    link = _link(message)
    assert link.startswith(f"/api/pesc/bill/{config_entry.entry_id}/{ACCOUNT_ID}/")
    assert "authSig=" in link
    assert not calls(aioclient_mock, "GET", FILE_URL)

    client = await hass_client_no_auth()
    resp = await client.get(link)
    assert resp.status == HTTPStatus.OK
    assert resp.content_type == "application/pdf"
    assert "inline" in resp.headers["Content-Disposition"]
    assert await resp.read() == PDF


async def test_view_requires_auth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    hass_client_no_auth,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)

    client = await hass_client_no_auth()
    resp = await client.get(
        f"/api/pesc/bill/{config_entry.entry_id}/{ACCOUNT_ID}/{BILL_ID}"
    )
    assert resp.status == HTTPStatus.UNAUTHORIZED
    assert not calls(aioclient_mock, "GET", UUID_URL)


async def test_view_unknown_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    hass_client,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)

    client = await hass_client()
    resp = await client.get(f"/api/pesc/bill/unknown/{ACCOUNT_ID}/{BILL_ID}")
    assert resp.status == HTTPStatus.NOT_FOUND


async def test_view_relogin(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    hass_client,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)
    aioclient_mock.clear_requests()
    mock_data(aioclient_mock)
    aioclient_mock.get(UUID_URL, status=401, json={"code": 5, "message": "auth"})
    aioclient_mock.post(AUTH_URL, json={"auth": "auth-2", "access": "access-2"})

    client = await hass_client()
    resp = await client.get(
        f"/api/pesc/bill/{config_entry.entry_id}/{ACCOUNT_ID}/{BILL_ID}"
    )

    assert resp.status == HTTPStatus.BAD_GATEWAY
    assert len(calls(aioclient_mock, "POST", AUTH_URL)) == 1


async def test_button_press_cannot_download(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(
        hass,
        aioclient_mock,
        config_entry,
        load_fixture("bill") | {"canDownload": False},
    )

    with pytest.raises(HomeAssistantError, match="недоступна"):
        await _press(hass)


async def test_button_press_api_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(BILL_URL, status=500, json={"code": "500", "message": "Ошибка"})

    with pytest.raises(HomeAssistantError, match="Ошибка"):
        await _press(hass)


async def test_view_relogin_success(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    hass_client,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)
    aioclient_mock.clear_requests()
    mock_data(aioclient_mock)
    aioclient_mock.post(AUTH_URL, json={"auth": "auth-2", "access": "access-2"})
    aioclient_mock.get(
        FILE_URL, content=PDF, headers={"content-type": "application/pdf"}
    )
    responses = [
        AiohttpClientMockResponse(
            "get", UUID_URL, status=401, json={"code": 5, "message": "auth"}
        ),
        AiohttpClientMockResponse(
            "get",
            UUID_URL,
            text=FILE_UUID,
            headers={"content-type": "application/json"},
        ),
    ]

    async def uuid_side_effect(method, url, data):
        return responses.pop(0)

    aioclient_mock.get(UUID_URL, side_effect=uuid_side_effect)

    client = await hass_client()
    resp = await client.get(
        f"/api/pesc/bill/{config_entry.entry_id}/{ACCOUNT_ID}/{BILL_ID}"
    )

    assert resp.status == HTTPStatus.OK
    assert await resp.read() == PDF
    assert len(calls(aioclient_mock, "POST", AUTH_URL)) == 1


@pytest.mark.parametrize("bill_id", ['1"2', "a%20b", "%D0%B9"])
async def test_view_invalid_bill_id(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    hass_client,
    bill_id: str,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)

    client = await hass_client()
    resp = await client.get(
        f"/api/pesc/bill/{config_entry.entry_id}/{ACCOUNT_ID}/{bill_id}"
    )

    assert resp.status == HTTPStatus.NOT_FOUND
    assert not calls(aioclient_mock, "GET", UUID_URL)


async def test_button_press_connection_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(BILL_URL, exc=aiohttp.ClientError("down"))

    with pytest.raises(HomeAssistantError, match="Ошибка соединения"):
        await _press(hass)


async def test_button_press_no_bill(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        BILL_URL, text="null", headers={"content-type": "application/json"}
    )

    with pytest.raises(HomeAssistantError, match="недоступна"):
        await _press(hass)


async def test_button_press_no_amount(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(
        hass, aioclient_mock, config_entry, load_fixture("bill") | {"amount": None}
    )

    message = await _press(hass)

    assert "Квитанция за сентябрь 2026" in message
    assert "на сумму" not in message


async def test_bill_button_kept_without_meters(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "button",
        const.DOMAIN,
        "pesc_999_bill",
        config_entry=config_entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )

    await _setup(hass, aioclient_mock, config_entry)

    kept = registry.async_get(entry.entity_id)
    assert kept is not None
    assert kept.disabled_by is er.RegistryEntryDisabler.USER


def _reauth_flows(hass: HomeAssistant) -> list:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_button_press_auth_error_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(BILL_URL, status=401, json={"code": 5, "message": "auth"})
    aioclient_mock.post(
        AUTH_URL,
        status=403,
        json={"code": "3", "message": "Неверный логин/пароль", "cause": ""},
    )

    with pytest.raises(HomeAssistantError):
        await _press(hass)
    await hass.async_block_till_done()

    assert len(_reauth_flows(hass)) == 1


async def test_view_auth_error_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    hass_client,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(UUID_URL, status=401, json={"code": 5, "message": "auth"})
    aioclient_mock.post(
        AUTH_URL,
        status=403,
        json={"code": "3", "message": "Неверный логин/пароль", "cause": ""},
    )

    client = await hass_client()
    resp = await client.get(
        f"/api/pesc/bill/{config_entry.entry_id}/{ACCOUNT_ID}/{BILL_ID}"
    )
    await hass.async_block_till_done()

    assert resp.status == HTTPStatus.BAD_GATEWAY
    assert len(_reauth_flows(hass)) == 1


@pytest.mark.parametrize("account_id", ["%C2%B2", "999"])
async def test_view_unknown_account(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    hass_client,
    account_id: str,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)

    client = await hass_client()
    resp = await client.get(
        f"/api/pesc/bill/{config_entry.entry_id}/{account_id}/{BILL_ID}"
    )

    assert resp.status == HTTPStatus.NOT_FOUND
    assert not any("/uuid" in str(call[1]) for call in aioclient_mock.mock_calls)


@pytest.mark.parametrize("body", ["null", '{"uuid": "x"}', ""])
async def test_view_invalid_uuid(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    hass_client,
    body: str,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        UUID_URL, text=body, headers={"content-type": "application/json"}
    )

    client = await hass_client()
    resp = await client.get(
        f"/api/pesc/bill/{config_entry.entry_id}/{ACCOUNT_ID}/{BILL_ID}"
    )

    assert resp.status == HTTPStatus.BAD_GATEWAY
    assert "недоступна" in await resp.text()
    assert not any("/v1/file/" in str(call[1]) for call in aioclient_mock.mock_calls)


async def test_button_press_escapes_period(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(
        hass,
        aioclient_mock,
        config_entry,
        load_fixture("bill") | {"timestamp": "<b>x</b>"},
    )

    message = await _press(hass)

    assert "<b>" not in message
    assert "&lt;b&gt;x&lt;/b&gt;" in message


async def test_button_press_actual_account_name(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)
    accounts = load_fixture("accounts")
    accounts[0]["alias"] = "Новое имя"
    aioclient_mock.clear_requests()
    # первый подходящий мок побеждает
    aioclient_mock.get(f"{API_URL}/v8/accounts", json=accounts)
    mock_data(aioclient_mock)
    aioclient_mock.get(BILL_URL, json=load_fixture("bill"))
    await hass.data[const.DOMAIN][config_entry.entry_id].async_refresh()

    await _press(hass)

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert notifications[f"pesc_bill_{ACCOUNT_ID}"]["title"] == "Новое имя"
