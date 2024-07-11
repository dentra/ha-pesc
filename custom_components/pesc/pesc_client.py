import json as jsonmod
import logging
from enum import StrEnum
from typing import Final, List, TypedDict

import aiohttp
from homeassistant import exceptions

_LOGGER = logging.getLogger(__name__)


class AccountAddress(TypedDict):
    identifier: bool
    value: str
    """
        Значение адреса.
        Возможно связано с полем identifier, при true возможен идентивикатор
    """


class AccountTenancyName(TypedDict):
    shorted: str
    """Сокращение. например: ЛС, ЕЛС"""

    fulled: str


class AccountTenancy(TypedDict):
    register: str
    """Номер счета."""

    name: AccountTenancyName


class AccountService(TypedDict):
    id: int
    """Идентиффикатор."""
    providerId: int
    payment: dict
    """
        "payment": {"type": "by_calculation_at_current","categories": []}
    """


class Account(TypedDict):
    id: int
    """Идентиффикатор."""

    alias: str
    """Наименование."""

    readingType: str
    """
        Способ получения счета.

        Возможные значения:
        * manual
        * auto
    """

    delivery: str
    """
        Способ доставки.
        Возможные значения:
        * PAPER
        * ELECTRONIC
    """

    address: AccountAddress
    """Адрес."""

    tenancy: AccountTenancy
    """
        Счет.

        Пример формирования названия счета:
            f'{account['tenancy']['name']['shorted']} № {account['tenancy']['register']}'
    """

    service: AccountService
    # fullName: str
    # confirmed: bool
    # autoPaymentOn: bool
    # role: dict

    # externalSystems  # unknown data type


class Group(TypedDict):
    id: int
    name: str
    accounts: List[int]


class MeterIndication(TypedDict):
    meterScaleId: int
    previousReading: int
    previousReadingDate: str
    scaleName: str
    unit: str
    # indicationId
    # registerReading


class MeterId(TypedDict):
    provider: int
    registration: str


class Meter(TypedDict):
    id: MeterId
    serial: str
    indications: List[MeterIndication]
    subserviceId: int
    status: str
    """
        ACTIVE
        AUTOMATED
    """


class ProfileName(TypedDict):
    first: str
    last: str
    patronymic: str | None


class Profile(TypedDict):
    phone: str
    email: str
    name: ProfileName


class UpdateValuePayload(TypedDict):
    scaleId: int
    value: int


class SubserviceUtility(StrEnum):
    ELECTRICITY = "ELECTRICITY"
    WATER = "WATER"
    GAS = "GAS"
    HEATING = "HEATING"
    UNKNOWN = "UNKNOWN"


class Subservice(TypedDict):
    description: str
    id: int
    name: str
    """ sample: "Электроэнергия" """
    type: str
    """ sample: "BASIC_WITH_VARIABLE_PRICE" """
    utility: SubserviceUtility


class PescClient:
    BASE_URL: Final = "https://ikus.pesc.ru"
    _API_URL: Final = f"{BASE_URL}/api"
    _APP_URL: Final = f"{BASE_URL}/application"

    def __init__(self, session: aiohttp.ClientSession, auth_token: str = None) -> None:
        self._session = session
        self._headers = {
            aiohttp.hdrs.ACCEPT: "application/json, text/plain, */*",
            aiohttp.hdrs.CONTENT_TYPE: "application/json",
        }
        self.token = auth_token
        self._inject_token(auth_token)

    def _inject_token(self, auth_token: str):
        if auth_token:
            self._headers[aiohttp.hdrs.AUTHORIZATION] = f"Bearer {auth_token}"

    async def _async_response_json(
        self, result: aiohttp.ClientResponse, nobodyrequest: bool = False
    ):
        try:
            if result.status != 200:
                if result.status == 404:
                    raise ClientError(
                        result.request_info,
                        {"code": 404, "message": f"Not Found: {result.url}"},
                    )
                json = await result.json()
                if "code" in json and int(json["code"]) == 5:
                    raise ClientAuthError(result.request_info, json)
                error = ClientError(result.request_info, json)
                _LOGGER.error(repr(error))
                raise error
            if nobodyrequest:
                return None

            if result.content_type == "application/json":
                return await result.json()
            else:
                _LOGGER.warning(
                    "Unknown content type: %s, content length %s",
                    result.content_type,
                    result.content_length,
                )
                return {}
        except aiohttp.ContentTypeError as err:
            raise ClientError(
                result.request_info, {"code": err.status, "message": err.message}
            ) from err

    async def _async_get_raw(self, url: str) -> aiohttp.ClientResponse:
        _LOGGER.debug("request: %s", url)
        return await self._session.get(f"{self._API_URL}{url}", headers=self._headers)

    async def _async_get(self, url: str):
        result = await self._async_get_raw(url)
        json = await self._async_response_json(result)
        if _LOGGER.isEnabledFor(logging.DEBUG):
            if isinstance(json, list) and len(json) > 100:
                json_str = "result is too large to display"
            else:
                json_str = "\n" + jsonmod.dumps(json, ensure_ascii=False, indent=None)
            _LOGGER.debug("%s: %s", url, json_str)
        return json

    async def async_login(self, username: str, password: str) -> str:
        headers = self._headers.copy()
        headers.pop(aiohttp.hdrs.AUTHORIZATION, None)
        headers["Captcha"] = "none"
        payload = {"type": "PHONE", "login": username, "password": password}
        result = await self._session.post(
            f"{self._API_URL}/v6/users/auth", headers=headers, json=payload
        )
        json = await self._async_response_json(result)
        self.token = json["auth"]
        self._inject_token(self.token)
        return self.token

    async def async_update_value(
        self,
        account_id: int,
        meter_id: str,
        payload: list[UpdateValuePayload],
    ) -> None:
        _LOGGER.debug(
            "Update account_id=%d, meter_id=%s, payload=%s",
            account_id,
            meter_id,
            payload,
        )
        result = await self._session.post(
            f"{self._API_URL}/v6/accounts/{account_id}/meters/{meter_id}/reading",
            headers=self._headers,
            json=payload,
        )
        await self._async_response_json(result, True)

    @property
    async def async_config(self):
        url = f"{self.BASE_URL}/config.json"
        result = await self._session.get(url)
        return await self._async_response_json(result)

    @property
    async def async_site_config(self):
        url = f"{self.BASE_URL}/site-config/config-mp-spb-fl.json"
        result = await self._session.get(url)
        return await self._async_response_json(result)

    async def async_accounts(self) -> List[Account]:
        return await self._async_get("/v8/accounts")

    async def async_reading_type(self, account_id: int) -> str:
        result = await self._async_get_raw(f"/v8/accounts/{account_id}/reading-types")
        return await result.text()

    async def async_address(self, account_id: int) -> AccountAddress:
        return await self._async_get(f"/v8/accounts/{account_id}/address")

    async def async_groups(self) -> List[Group]:
        return await self._async_get("/v6/accounts/groups")

    async def async_meters(self, account_id: int) -> List[Meter]:
        return await self._async_get(f"/v6/accounts/{account_id}/meters/info")

    async def async_profile(self) -> Profile:
        # return await self._async_get("/v3/profile")
        return await self._async_get("/v6/users/current")

    async def async_subservices(self, provider_id: int) -> List[Subservice]:
        return await self._async_get(
            f"/v7/accounts/providers/{provider_id}/subservices"
        )

    # async def async_groups(self) -> List[IkusPescGroup]:
    #     return await self._async_get("/v3/groups")

    # async def async_accounts(self, group_id: int) -> List[IkusPescAccount]:
    #     return await self._async_get(f"/v5/groups/{group_id}/accounts")

    # async def async_providers(self):
    #     return await self._async_get("/v7/providers")

    # async def async_data(self, account_id: int):
    #     return await self._async_get(f"/v5/accounts/{account_id}/data")

    # async def async_usual(self, account_id, meter_id):
    #     return await self._async_get(
    #         f"/v3/accounts/{account_id}/{meter_id}/consumption/usual"
    #     )

    # async def async_common_info(self, account_id: int):
    #     return await self._async_get(f"/v4/accounts/{account_id}/common-info")

    # async def async_apartment_info(self, account_id: int):
    #     return await self._async_get(f"/v3/accounts/{account_id}/apartment-info")

    # async def async_meter_info(self, account_id: int, meter_id):
    #     return await self._async_get(
    #         f"/v3/accounts/{account_id}/meters/{meter_id}/meter-info"
    #     )

    async def async_tariff(self, account_id: int):
        return await self._async_get(f"/v3/accounts/{account_id}/tariff")

    async def async_details(self, account_id: int):
        return await self._async_get(f"/v7/accounts/{account_id}/details")


class ClientError(exceptions.HomeAssistantError):
    def __init__(self, request_info: aiohttp.RequestInfo, json: dict) -> None:
        super().__init__(self.__class__.__name__)
        self.request_info = request_info
        self.json = json

    @property
    def code(self):
        return self.json["code"]

    @property
    def message(self):
        return self.json["message"]

    @property
    def cause(self) -> str | None:
        return self.json.get("cause")

    def __repr__(self) -> str:
        res = self.__class__.__name__
        res += "["
        res += f"url={self.request_info.url}"
        res += f", code={self.code}"
        res += f", message={self.message}"
        if self.cause is not None:
            res += f", cause={self.cause}"
        res += "]"
        return res

    def __str__(self) -> str:
        return self.message


class ClientAuthError(ClientError):
    pass
