import asyncio
import logging
from datetime import datetime
from typing import Dict, Final, List

from homeassistant.util import slugify

from . import pesc_client

_LOGGER = logging.getLogger(__name__)


class Account:
    __slots__ = ("id", "name", "type", "tenancy", "address", "service_provider_id")

    def __init__(self, account: pesc_client.Account) -> None:
        self.id = account["id"]
        self.name = account["alias"]
        self.type = None
        self.tenancy = f"{account['tenancy']['name']['shorted']} № {account['tenancy']['register']}"
        self.address = None
        self.service_provider_id = account["service"]["providerId"]

    def __repr__(self) -> str:
        return (
            self.__class__.__name__
            + f"[id={self.id}, name={self.name}, type={self.type}, "
            + f"tenancy={self.tenancy}, address={self.address}]"
        )


class Meter:
    __slots__ = ("id", "serial", "subservice_id")

    def __init__(
        self,
        meter: pesc_client.Meter,
    ) -> None:
        self.id = meter["id"]["registration"]
        self.serial = meter["serial"]
        self.subservice_id = meter["subserviceId"]

    def __repr__(self) -> str:
        return (
            self.__class__.__name__
            + f"[id={self.id}, serial={self.serial},subservice_id={self.subservice_id}]"
        )


class MeterInd:
    __slots__ = ("value", "date", "unit", "name", "scale_id", "account", "meter")

    def __init__(
        self,
        account: Account,
        meter: Meter,
        ind: pesc_client.MeterIndication,
    ) -> None:
        self.value = ind["previousReading"]
        self.date = datetime.strptime(ind["previousReadingDate"], "%d.%m.%Y").date()
        self.unit = ind["unit"]
        self.name = ind["scaleName"]
        self.scale_id = ind["meterScaleId"]
        self.account = account
        self.meter = meter

    @property
    def id(self) -> str:
        return f"{self.meter.id}_{self.scale_id}"

    @property
    def auto(self) -> bool:
        return self.account.type == "auto"

    def __repr__(self) -> str:
        return (
            self.__class__.__name__
            + f"[id={self.id}, name={self.name}, date={self.date}, "
            + f"scale_id={self.scale_id}, value={self.value}, unit={self.unit}, "
            + f"meter={self.meter}, account={self.account}]"
        )


class Group:
    __slots__ = ("id", "name", "accounts")

    def __init__(self, group: pesc_client.Group) -> None:
        self.id = group["id"]
        self.name = group["name"]
        self.accounts = group["accounts"]

    def __repr__(self) -> str:
        return (
            self.__class__.__name__
            + f"[id={self.id}, name={self.name}, accounts={self.accounts}]"
        )


class TariffRate:
    """Тарифная ставка"""

    __slots__ = ("value", "name", "detail", "description")

    value: float | str
    """Значение, например: 5.30"""

    name: str
    """Название, например: День"""

    detail: str
    """Детализация, например: 07:00 — 23:00"""

    description: str
    """Описание, например: Тариф 1 диапазона потребления"""

    def __init__(
        self, value: float | str, name: str, detail: str = "", description: str = ""
    ) -> None:
        self.value = value
        self.name = name
        self.detail = detail
        self.description = description

    def __repr__(self) -> str:
        return (
            self.__class__.__name__
            + f"[name={self.name}, value={self.value}, detail={self.detail}, description={self.detail}]"
        )


class Tariff:
    __slots__ = ("name", "kind", "rates")

    name: str
    """ Например: "Холодное водоснабжение" или "Горячее водоснабжение" и т.д. """

    kind: str | None
    """ Тип, например: "Двухтарифный" """

    rates: list[TariffRate]

    def __init__(self, name: str, kind: str, rates: list[TariffRate]) -> None:
        self.name = name
        self.kind = kind
        self.rates = rates

    def __repr__(self) -> str:
        return (
            self.__class__.__name__
            + f"[name={self.name}, kind={self.kind}, rates={self.rates}]"
        )

    def rate(self, meter: MeterInd) -> TariffRate | None:
        if len(self.rates) == 0:
            return None

        if self.name in [
            "Холодное водоснабжение",
            "Горячее водоснабжение",
            "ГВС",
            "Водоотведение ХВС",
            "Водоотведение ГВС",
        ]:
            return self.rates[0]

        for rate in self.rates:
            if rate.name == meter.name:
                return rate

        if self.name == "Электроэнергия":
            if self.kind == "Двухтарифный" and len(self.rates) == 2:
                if meter.scale_id == 2:
                    return self.rates[0]
                if meter.scale_id == 3:
                    return self.rates[1]
            elif self.kind == "Однотарифный" and len(self.rates) == 2:
                return self.rates[0]
            else:
                _LOGGER.warning(
                    'Unsupported tariff "%s", kind "%s", rates: %s',
                    self.name,
                    self.kind,
                    self.rates,
                )

        if len(self.rates) == 1:
            return self.rates[0]

        return TariffRate("/".join([rate for rate in self.rates]), "unknown")


class PescApi:
    _profile: pesc_client.Profile | None = None
    _meters: List[MeterInd] = []
    _groups: List[Group] = []
    _tariffs: Dict[int, list[Tariff]] = {}
    _subservices: Dict[int, pesc_client.Subservice] = {}

    def __init__(self, client: pesc_client.PescClient) -> None:
        # _LOGGER.debug("Initialize %s", client.token)
        self._client = client
        # overwrite client to ensure it is FakeClient
        self.client = FakeClient() if client.token == FakeClient.TOKEN else self._client

    async def async_login(self, username: str, password: str) -> str:
        _LOGGER.debug("Login %s", username)
        self.client = self._client
        if username.startswith("test") and password == "test":
            self.client = FakeClient()
        return await self.client.async_login(username, password)

    async def async_update_value(
        self, meter: MeterInd, values: list[pesc_client.UpdateValuePayload]
    ) -> list[pesc_client.UpdateValuePayload]:
        _LOGGER.debug("Update %s %s to %s", meter.account.name, meter.name, values)
        meters = [mtr for mtr in self.meters if mtr.meter.id == meter.meter.id]
        if len(values) != len(meters):
            for mtr in meters:
                found = [val for val in values if mtr.scale_id == val["scaleId"]]
                if not found:
                    values.append(
                        pesc_client.UpdateValuePayload(
                            scaleId=mtr.scale_id, value=mtr.value
                        )
                    )

        await self.client.async_update_value(meter.account.id, meter.meter.id, values)
        return values

    async def async_fetch_all(self) -> None:
        """Fetch profile and data."""
        await self.async_fetch_profile()
        await self.async_fetch_data()

    async def async_fetch_profile(self) -> None:
        _LOGGER.debug("Fetch profile")
        self._profile = None
        self._profile = await self.client.async_profile()

    async def async_fetch_data(self) -> None:
        """
        Fetch data.

        Exceptions:
          - pesc_client.ClientAuthError
          - pesc_client.ClientError
        """
        _LOGGER.debug("Fetch data")
        self._meters.clear()
        self._groups.clear()
        self._tariffs.clear()
        self._subservices.clear()

        accounts = await self.client.async_accounts()
        await asyncio.gather(*(self._load_account(account) for account in accounts))

    async def _load_account(self, account: pesc_client.Account):
        acc = Account(account)
        _LOGGER.debug("Got %s", acc)
        await asyncio.gather(
            self._load_reading_types(acc),
            self._load_address(acc),
            self._load_meters(acc),
            self._load_tariffs(acc),
        )
        # load subservices after meters to store only requred subservices
        await self._load_subservices(acc)

    async def _load_reading_types(self, acc: Account):
        acc.type = await self.client.async_reading_type(acc.id)

    async def _load_address(self, acc: Account):
        address = await self.client.async_address(acc.id)
        if address and "value" in address:
            acc.address = address["value"]

    async def _load_meters(self, acc: Account):
        meters = await self.client.async_meters(acc.id)
        for meter in meters:
            met = Meter(meter)
            for met_ind in meter["indications"]:
                ind = MeterInd(acc, met, met_ind)
                self._meters.append(ind)
                _LOGGER.debug("Got %s", ind)

    def _process_tariff_detail(self, detail: dict):
        def find_any(json_list, res, key, val) -> str:
            for json in json_list:
                if json[key] == val:
                    return json[res]
            return None

        def find_named_val(json_list, key) -> str:
            return find_any(json_list, "value", "name", key)

        block_type = detail["blockType"]
        content = detail["content"]
        if block_type == "SOLID":
            if rates := find_named_val(content, "Тарифная ставка"):
                return Tariff(
                    detail["header"],
                    find_named_val(content, "Тариф"),
                    [
                        TariffRate(float(value), "")
                        for value in rates.replace(",", ".").split("/")
                    ],
                )
        elif block_type == "TABLE":
            if kind := find_named_val(content, "Тип тарифа"):
                return Tariff(
                    detail["header"],
                    kind,
                    [
                        TariffRate(
                            float(json["columns"][0]["value"]),
                            json["name"],
                            json["description"],
                            find_any(
                                detail["columns"],
                                "name",
                                "code",
                                json["columns"][0]["code"],
                            ),
                        )
                        for json in content
                        if json["name"] != "Тип тарифа"
                    ],
                )

        _LOGGER.debug("Unsupported block: %s", detail["header"])

        return None

    async def _load_tariffs(self, acc: Account):
        try:
            for detail in await self.client.async_details(acc.id):
                tariff = self._process_tariff_detail(detail)
                if not tariff:
                    continue
                if acc.id not in self._tariffs:
                    self._tariffs[acc.id] = []
                self._tariffs[acc.id].append(tariff)
                _LOGGER.debug("Got %s", tariff)
        except pesc_client.ClientError:
            # no details returned. iе is sometimes normal
            pass

    async def _load_subservices(self, acc: Account):
        try:
            subservices = await self._client.async_subservices(acc.service_provider_id)
            for subservice in subservices:
                for meter in self._meters:
                    if meter.meter.subservice_id == subservice["id"]:
                        self._subservices[subservice["id"]] = subservice
                        break

        except pesc_client.ClientError as err:
            _LOGGER.error(
                "Failed load subservices for service provider %d: %s",
                acc.service_provider_id,
                err,
            )

    async def async_fetch_groups(self) -> None:
        _LOGGER.debug("Fetch groups")
        groups = await self.client.async_groups()
        self._groups = (Group(group) for group in groups)

    @property
    def profile_id(self) -> str | None:
        if not self._profile:
            return None
        phone = self._profile["phone"]
        if not phone or len(phone) == 0:
            return slugify(self._profile["email"])
        if phone[0] == "+":
            return phone[1:]
        return slugify(phone)

    @property
    def profile_name(self) -> str | None:
        if not self._profile:
            return None
        name = self._profile["name"]
        name = " ".join(filter(None, [name["last"], name["first"], name["patronymic"]]))
        if len(name) > 0:
            return name
        if self._profile["email"] and len(self._profile["email"]) > 0:
            return self._profile["email"]
        return self._profile["phone"]

    @property
    def meters(self) -> List[MeterInd]:
        """
        Returns meters without duplicates. A values with an older account will be skipped.
        """
        meters: dict[str, MeterInd] = {}
        for meter in sorted(self._meters, key=lambda x: f"{x.account.id}_{x.id}"):
            meters[meter.id] = meter
        return list(meters.values())

    @property
    def groups(self) -> List[Group]:
        return self._groups

    def tariff(self, ind: MeterInd) -> Tariff | None:
        subservice = self.subservice(ind.meter.subservice_id)
        if not subservice:
            return None
        tariffs = self._tariffs.get(ind.account.id, [])
        for tariff in tariffs:
            if tariff.name == subservice["name"]:
                return tariff
        return None

    def find_ind(self, ind_id: str) -> MeterInd | None:
        for ind in self.meters:
            if ind.id == ind_id:
                return ind
        return None

    def subservice(self, subservice_id: int) -> pesc_client.Subservice | None:
        return self._subservices.get(subservice_id, None)


class FakeClient(pesc_client.PescClient):
    TOKEN: Final = "ABC-TEST-DEF"

    _accounts: List[pesc_client.Account] = []
    _groups: List[pesc_client.Group] = []
    _meters: dict[int, List[pesc_client.Meter]] = {}

    def __init__(self):
        super().__init__(None, None)

        self.token = self.TOKEN

        for i in range(3):
            account: pesc_client.Account = {
                "id": i,
                "alias": f"Аккаунт {i}",
                "readingType": "auto" if i == 1 else "manual",
                "address": {"identifier": False, "value": f"ул Ленина, {i}"},
                "tenancy": {
                    "name": {"shorted": "ЕЛС" if i == 0 else "ЛС"},
                    "register": f"000/00{i}",
                },
            }

            self._accounts.append(account)

            if i == 1:
                self._groups[0]["accounts"].append(account["id"])
            else:
                self._groups.append(
                    {"id": i, "name": f"Группа {i}", "accounts": [account["id"]]}
                )

            self._meters[i] = []

            meter = pesc_client.Meter(
                {
                    "id": {
                        "provider": 1,
                        "registration": "111111" if i == 1 else "000000",
                    },
                    "serial": "111111" if i == 1 else "000000",
                    "indications": [],
                }
            )
            for ind in range(2):
                ind = pesc_client.MeterIndication(
                    {
                        "unit": "кВт*ч",
                        "previousReadingDate": "23.01.2023",
                        "previousReading": (ind + 1) * 1000,
                        "scaleName": "День" if ind % 2 != 0 else "Ночь",
                        "meterScaleId": 2 if ind % 2 != 0 else 3,
                    },
                )
                meter["indications"].append(ind)
            self._meters[i].append(meter)

    async def async_login(self, username: str, password: str) -> str:
        return self.token

    async def async_update_value(
        self,
        account_id: int,
        meter_id: str,
        payload: list[pesc_client.UpdateValuePayload],
    ) -> None:
        _LOGGER.debug(
            "Update account_id=%d, meter_id=%s, payload=%s",
            account_id,
            meter_id,
            payload,
        )
        meters = self._meters[account_id]
        for meter in meters:
            _LOGGER.debug("meter %s", meter)
            if meter["id"]["registration"] == meter_id:
                for ind in meter["indications"]:
                    if ind["meterScaleId"] == payload["scale_id"]:
                        _LOGGER.debug("Found %s", ind)
                        ind["previousReading"] = payload["value"]
                        ind["previousReadingDate"] = datetime.now().strftime("%d.%m.%Y")

    async def async_accounts(self) -> List[pesc_client.Account]:
        return self._accounts

    async def async_groups(self) -> List[pesc_client.Group]:
        return self._groups

    async def async_meters(self, account_id: int) -> List[pesc_client.Meter]:
        return self._meters[account_id] or []

    async def async_profile(self) -> pesc_client.Profile:
        return {
            "email": "a@b.c",
            "phone": "+71234567890",
            "name": {"last": "Иванов", "first": "Иван", "patronymic": None},
        }
