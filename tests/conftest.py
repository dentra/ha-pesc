import json
from pathlib import Path

import pytest
from aiohttp import RequestInfo
from multidict import CIMultiDict, CIMultiDictProxy
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.pesc import const

# клиент использует атрибуты, которых нет у мока
AiohttpClientMockResponse.request_info = property(
    lambda self: RequestInfo(self.url, self.method, CIMultiDictProxy(CIMultiDict()))
)
AiohttpClientMockResponse.content_length = property(lambda self: len(self.response))
AiohttpClientMockResponse.content_type = property(
    lambda self: self._headers.get("content-type", "application/json")
)

BASE_URL = "https://ikus.pesc.ru"
API_URL = f"{BASE_URL}/api"
AUTH_URL = f"{API_URL}/v8/users/auth"
TRANSACTION_ID = "tid-1"
ACCOUNT_ID = 100001
PROVIDER_ID = 1

FIXTURES = Path(__file__).parent / "fixtures"

AUTH = {"auth": "auth-1", "access": "access-1", "verified": "verified-1"}


def load_fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def calls(aioclient_mock: AiohttpClientMocker, method: str, url: str) -> list:
    return [
        call
        for call in aioclient_mock.mock_calls
        if call[0].upper() == method and str(call[1]) == url
    ]


def mock_data(aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{API_URL}/v6/users/current", json=load_fixture("profile"))
    aioclient_mock.get(f"{API_URL}/v8/accounts", json=load_fixture("accounts"))
    aioclient_mock.get(
        f"{API_URL}/v8/accounts/{ACCOUNT_ID}/reading-types", text="manual"
    )
    aioclient_mock.get(
        f"{API_URL}/v8/accounts/{ACCOUNT_ID}/address", json=load_fixture("address")
    )
    aioclient_mock.get(
        f"{API_URL}/v6/accounts/{ACCOUNT_ID}/meters/info", json=load_fixture("meters")
    )
    aioclient_mock.get(
        f"{API_URL}/v7/accounts/{ACCOUNT_ID}/details", json=load_fixture("details")
    )
    aioclient_mock.get(
        f"{API_URL}/v7/accounts/providers/{PROVIDER_ID}/subservices",
        json=load_fixture("subservices"),
    )


def mock_2fa_required(aioclient_mock: AiohttpClientMocker, types: list[str]) -> None:
    aioclient_mock.post(
        AUTH_URL, status=424, json={"transactionId": TRANSACTION_ID, "types": types}
    )


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=const.DOMAIN,
        version=const.CONFIG_VERSION,
        unique_id="pesc_70000000000",
        title="Иванов Иван",
        data={
            const.CONF_AUTH: dict(AUTH),
            const.CONF_LOGIN_TYPE: "phone",
            const.CONF_USERNAME: "+70000000000",
            const.CONF_PASSWORD: "secret",
        },
    )
