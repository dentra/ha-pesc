import copy

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.pesc import const, pesc_client

from .conftest import API_URL, AUTH, load_fixture, mock_data

ACCOUNT_ID_2 = 100002
METER_ID_2 = "00000XYZ34"
AUTH_2 = {"auth": "auth-b", "access": "access-b", "verified": "verified-b"}


def _account_2() -> dict:
    account = load_fixture("accounts")[0]
    account.update(id=ACCOUNT_ID_2, alias="Дача")
    account["tenancy"]["register"] = "0000100002"
    return account


def _meters_2() -> list:
    meters = copy.deepcopy(load_fixture("meters"))
    meters[0]["id"]["registration"] = METER_ID_2
    return meters


@pytest.fixture
async def entries(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MockConfigEntry, MockConfigEntry]:
    profile = load_fixture("profile")
    accounts = {AUTH["auth"]: load_fixture("accounts"), AUTH_2["auth"]: [_account_2()]}

    async def async_accounts(self):
        return accounts[self.auth[pesc_client.AUTH_AUTH]]

    async def async_profile(self):
        return profile

    monkeypatch.setattr(pesc_client.PescClient, "async_accounts", async_accounts)
    monkeypatch.setattr(pesc_client.PescClient, "async_profile", async_profile)

    mock_data(aioclient_mock)
    aioclient_mock.get(
        f"{API_URL}/v8/accounts/{ACCOUNT_ID_2}/reading-types", text="manual"
    )
    aioclient_mock.get(
        f"{API_URL}/v8/accounts/{ACCOUNT_ID_2}/address",
        json=load_fixture("address"),
    )
    aioclient_mock.get(
        f"{API_URL}/v6/accounts/{ACCOUNT_ID_2}/meters/info", json=_meters_2()
    )
    aioclient_mock.get(
        f"{API_URL}/v7/accounts/{ACCOUNT_ID_2}/details", json=load_fixture("details")
    )

    entry_2 = MockConfigEntry(
        domain=const.DOMAIN,
        version=const.CONFIG_VERSION,
        unique_id="pesc_70000000001",
        title="Дача",
        data={**config_entry.data, const.CONF_AUTH: dict(AUTH_2)},
    )
    for entry in (config_entry, entry_2):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    return config_entry, entry_2


async def test_data_isolated(
    hass: HomeAssistant, entries: tuple[MockConfigEntry, MockConfigEntry]
) -> None:
    for entry, account_id in zip(entries, (100001, ACCOUNT_ID_2)):
        meters = hass.data[const.DOMAIN][entry.entry_id].api.meters
        assert {meter.account.id for meter in meters} == {account_id}


@pytest.mark.parametrize(
    ("entity_id", "url"),
    [
        (
            "sensor.pesc_00000abc12_2",
            f"{API_URL}/v7/accounts/100001/meters/00000ABC12/reading",
        ),
        (
            "sensor.pesc_00000xyz34_2",
            f"{API_URL}/v7/accounts/{ACCOUNT_ID_2}/meters/{METER_ID_2}/reading",
        ),
    ],
)
async def test_update_value_any_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entries: tuple[MockConfigEntry, MockConfigEntry],
    entity_id: str,
    url: str,
) -> None:
    aioclient_mock.post(url)

    response = await hass.services.async_call(
        const.DOMAIN,
        const.SERVICE_UPDATE_VALUE,
        {"entity_id": entity_id, "value": 12346},
        blocking=True,
        return_response=True,
    )

    assert response["code"] == 0
    assert [call[2] for call in aioclient_mock.mock_calls if str(call[1]) == url] == [
        [{"scaleId": 2, "value": 12346}, {"scaleId": 3, "value": 6789.0}]
    ]
