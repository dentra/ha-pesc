import json as jsonmod
import logging
from enum import StrEnum
from typing import Any, Final, List, Optional, TypedDict

import aiohttp
from homeassistant import exceptions

_LOGGER = logging.getLogger(__name__)

LOGIN_TYPE_PHONE: Final = "phone"
LOGIN_TYPE_EMAIL: Final = "email"

AUTH_AUTH: Final = "auth"
AUTH_VERIFIED: Final = "verified"
AUTH_ACCESS: Final = "access"

CONFIRMATION_SMS: Final = "PHONE"
CONFIRMATION_EMAIL: Final = "EMAIL"
CONFIRMATION_CALL: Final = "FLASHCALL"


class AccountAddress(TypedDict):
    identifier: bool
    value: str
    """
        Значение адреса.
        Возможно связано с полем identifier, при true возможен идентификатор
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
    """Идентификатор."""
    providerId: int
    payment: dict
    """
        "payment": {"type": "by_calculation_at_current","categories": []}
    """


class Account(TypedDict):
    id: int
    """Идентификатор."""

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
    patronymic: Optional[str]


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


class UserAuth(TypedDict):
    auth: str
    verified: str
    access: str


class UserAuthTransaction(TypedDict):
    transactionId: str
    types: list[str]
    confirmation_type: str


class PescClient:
    BASE_URL: Final = "https://ikus.pesc.ru"
    _API_URL: Final = f"{BASE_URL}/api"
    _APP_URL: Final = f"{BASE_URL}/application"

    def __init__(
        self, session: aiohttp.ClientSession, auth: Optional[UserAuth] = None
    ) -> None:
        self._session = session
        self._headers = {
            aiohttp.hdrs.ACCEPT: "application/json, text/plain, */*",
            aiohttp.hdrs.CONTENT_TYPE: "application/json",
            "Customer": "ikus-spb",
        }
        self.auth = auth
        if auth:
            self._updata_auth(auth)

    def _updata_auth(self, auth: UserAuth):
        self.auth = auth
        if AUTH_AUTH in auth:
            self._headers[aiohttp.hdrs.AUTHORIZATION] = f"Bearer {auth[AUTH_AUTH]}"

    async def _async_response_json(
        self, result: aiohttp.ClientResponse, empty_body_request: bool = False
    ):
        try:
            if result.status != 200:
                if result.status == 404:
                    raise ClientError(
                        result.request_info, code=404, message="Страница не найдена"
                    )
                json = await result.json()
                if "code" in json and int(json["code"]) == 5:
                    raise ClientAuthError(result.request_info, json)
                error = ClientError(result.request_info, json)
                _LOGGER.error("Failed request: %s", error)
                raise error
            if empty_body_request:
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
                result.request_info, code=err.status, message=err.message
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
        """deprecated"""
        headers = self._headers.copy()
        headers.pop(aiohttp.hdrs.AUTHORIZATION, None)
        headers["Captcha"] = "none"
        payload = {"type": "PHONE", "login": username, "password": password}
        result = await self._session.post(
            f"{self._API_URL}/v7/users/auth", headers=headers, json=payload
        )
        json = await self._async_response_json(result)
        self._updata_auth(json)
        return json[AUTH_AUTH]

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
            f"{self._API_URL}/v7/accounts/{account_id}/meters/{meter_id}/reading",
            headers=self._headers,
            json=payload,
        )
        await self._async_response_json(result, True)

    @property
    async def async_config(self) -> dict[str, Any]:
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

    async def async_users_logout(self):
        # вызывать метод DELETE на https://ikus.pesc.ru/api/v6/users/auth
        # в заголовках Bearer-токен
        # payload: {"access":"...","auth":"...","verified":"..."}
        pass

    def can_reauth(self, auth: UserAuth) -> bool:
        return AUTH_VERIFIED in auth

    async def async_users_reauth(
        self, username: str, password: str, auth: UserAuth, type: str = "PHONE"
    ) -> UserAuth:
        """
        возвращает json c полями access и auth.

        * auth - используется в качестве Bearer токена для дальнейших запросов.
        * access - назначение пока не понятно

        пример:
          {access: "...", "auth": "..."}
        """
        if not self.can_reauth(auth):
            raise ClientAuthError(None, message="Отсутствует токен verified")
        headers = self._headers.copy()
        headers.pop(aiohttp.hdrs.AUTHORIZATION, None)
        headers["Captcha"] = "none"
        headers["Auth-verification"] = auth[AUTH_VERIFIED]

        payload = {"login": username, "password": password, "type": type}
        result = await self._session.post(
            f"{self._API_URL}/v8/users/auth", headers=headers, json=payload
        )
        # Ожидаемый статус 424
        if result.status != 200:
            raise ClientError(
                result.request_info,
                code=result.status,
                message="Неожиданный статус ответа повторной авторизации",
            )
        json = await result.json()
        self._updata_auth(json)
        return json

    async def async_users_auth(
        self, username: str, password: str, login_type: str = "PHONE"
    ) -> UserAuthTransaction:
        """
        возвращает json c идентификатором транзакции [transactionId] и массивом строк с типами подтверждения [types].

        в случае неудаче бросает исключение ClientError.

        пример:
         {"transactionId": "489597b6-9614-46fb-ba6e-5629ad88dfed", "types": ["EMAIL","PHONE","FLASHCALL"]}
        """
        headers = self._headers.copy()
        headers.pop(aiohttp.hdrs.AUTHORIZATION, None)
        headers["Captcha"] = "none"
        result = await self._session.post(
            f"{self._API_URL}/v8/users/auth",
            headers=headers,
            json={"login": username, "password": password, "type": login_type},
        )
        # Ожидаемый статус 424
        if result.status != 424:
            json = {
                "code": result.status,
                "message": "Неожиданный статус ответа авторизации",
            }
            try:
                body = await result.json()
                if body.get("message"):
                    json = body
            except Exception:
                pass
            raise ClientError(result.request_info, json)
        json = await result.json()
        return json

    async def async_users_check_confirmation_send(
        self, auth_transaction: UserAuthTransaction, confirmation_type="PHONE"
    ) -> UserAuthTransaction:
        transaction_id = auth_transaction["transactionId"]

        headers = self._headers.copy()
        headers.pop(aiohttp.hdrs.AUTHORIZATION, None)
        headers[aiohttp.hdrs.REFERER] = (
            f"https://ikus.pesc.ru/auth/{transaction_id}/verify"
        )

        result = await self._session.post(
            f"{self._API_URL}/v7/users/{transaction_id}/{confirmation_type.lower()}/check/confirmation/send",
            headers=headers,
            json={},
        )
        # Ожидаемый статус 200
        if result.status != 200:
            json = {
                "code": result.status,
                "message": "Неожиданный статус ответа запроса кода подтверждения",
            }
            try:
                body = await result.json()
                if body.get("message"):
                    json = body
            except Exception:
                pass
            raise ClientError(result.request_info, json)
        # тело ответа значения не имеет
        auth_transaction["confirmation_type"] = confirmation_type.lower()
        return auth_transaction

    async def async_users_check_verification(
        self, auth_transaction: UserAuthTransaction, code: str
    ) -> UserAuth:
        """
        возвращает json c полями access, auth и verified.

        * auth - используется в качестве Bearer токена для дальнейших запросов.
        * verified - используется для повторной авторизации без запроса второго фактора.
        * access - назначение пока не понятно

        пример:
          {access: "...", "auth": "...", "verified": "..."}
        """
        transaction_id = auth_transaction["transactionId"]
        confirmation_type = auth_transaction["confirmation_type"]

        headers = self._headers.copy()
        headers.pop(aiohttp.hdrs.AUTHORIZATION, None)
        headers[aiohttp.hdrs.REFERER] = (
            f"https://ikus.pesc.ru/auth/{transaction_id}/verify"
        )
        headers["Customer"] = "ikus-spb"

        result = await self._session.post(
            f"{self._API_URL}/v7/users/{transaction_id}/{confirmation_type}/check/verification",
            headers=headers,
            json={"code": code},
        )
        # Ожидаемый статус 200
        if result.status != 200:
            json = {
                "code": result.status,
                "message": "Неожиданный статус ответа подтверждения кода",
            }
            try:
                body = await result.json()
                if body.get("message"):
                    json = body
            except Exception:
                pass
            raise ClientError(result.request_info, json)
        json = await result.json()
        self._updata_auth(json)
        return json

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
    def __init__(
        self,
        info: aiohttp.RequestInfo,
        json: Optional[dict] = None,
        code: Optional[int] = None,
        message: str = "",
    ) -> None:
        super().__init__(self.__class__.__name__)
        self.info = info
        self.json = json if json else {}
        if code is not None:
            self.json["code"] = code
        if message:
            self.json["message"] = message

    @property
    def code(self) -> int:
        return self.json.get("code", -1)

    @property
    def message(self) -> str:
        return self.json.get("message", "Неизвестная ошибка")

    @property
    def cause(self) -> str:
        return self.json.get("cause", "")

    def __repr__(self) -> str:
        res = self.__class__.__name__
        res += "["
        if self.info:
            res += f"url={self.info.url}"
        if "code" in self.json:
            res += f", code={self.json['code']}"
        if "message" in self.json:
            res += f", message={self.json['message']}"
        if "cause" in self.json:
            res += f", cause={self.json['cause']}"
        res += "]"
        return res

    def __str__(self) -> str:
        res = self.message
        if "code" in self.json:
            res = f"{res}, код {self.json['code']}"
        if self.info:
            res = f"{res} ({self.info.method} {self.info.url})"
        return res


class ClientAuthError(ClientError):
    pass
