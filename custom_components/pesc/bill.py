"""Bill download routines"""

import asyncio
import datetime
import html
import logging
from http import HTTPStatus

import aiohttp
from aiohttp import web
from homeassistant.components import persistent_notification
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.http import KEY_HASS

from . import const, pesc_api, pesc_client
from .coordinator import PescDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

URL = "/api/pesc/bill/{entry_id}/{account_id}/{bill_id}"
LINK_EXPIRATION = datetime.timedelta(days=1)
TIMEOUT = 30

MONTHS = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


def _period(timestamp: str) -> str:
    try:
        date = datetime.datetime.strptime(timestamp, "%d.%m.%Y %H:%M:%S")
    except TypeError, ValueError:
        return str(timestamp)
    return f"{MONTHS[date.month - 1]} {date.year}"


def _amount(amount) -> str:
    if not isinstance(amount, (int, float)):
        return ""
    return f" на сумму {amount:.2f} ₽".replace(".", ",")


def _valid_bill_id(bill_id) -> bool:
    return isinstance(bill_id, str) and bill_id.isascii() and bill_id.isalnum()


def _account(
    coordinator: PescDataUpdateCoordinator, account_id: int
) -> pesc_api.Account | None:
    for ind in coordinator.api.meters:
        if ind.account.id == account_id:
            return ind.account
    return None


async def async_notify_bill(
    hass: HomeAssistant, coordinator: PescDataUpdateCoordinator, account_id: int
) -> None:
    """Create a notification with a signed link to the current bill PDF."""
    if (account := _account(coordinator, account_id)) is None:
        raise HomeAssistantError("Лицевой счёт не найден")

    try:
        async with asyncio.timeout(TIMEOUT):
            bill = await coordinator.async_with_relogin(
                lambda: coordinator.api.client.async_current_bill(account.id)
            )
    except pesc_client.ClientAuthError as err:
        coordinator.config_entry.async_start_reauth(hass)
        raise HomeAssistantError(err.message) from err
    except pesc_client.ClientError as err:
        raise HomeAssistantError(err.message) from err
    except (aiohttp.ClientError, TimeoutError) as err:
        raise HomeAssistantError(f"Ошибка соединения: {err}") from err

    if (
        not isinstance(bill, dict)
        or not bill.get("canDownload")
        or not _valid_bill_id(bill.get("id"))
    ):
        raise HomeAssistantError("Квитанция недоступна для скачивания")

    path = URL.format(
        entry_id=coordinator.config_entry.entry_id,
        account_id=account.id,
        bill_id=bill["id"],
    )
    # уведомление видят все, ссылка не должна зависеть от сессии нажавшего
    link = async_sign_path(hass, path, LINK_EXPIRATION, use_content_user=True)
    persistent_notification.async_create(
        hass,
        # свой адрес фронтенд открывает внутри приложения, нужна новая вкладка
        f'<a href="{html.escape(link)}" target="_blank">'
        f"Квитанция за {html.escape(_period(bill.get('timestamp')))}</a>"
        f"{_amount(bill.get('amount'))}",
        title=account.name,
        notification_id=f"{const.DOMAIN}_bill_{account.id}",
    )


class PescBillView(HomeAssistantView):
    url = URL
    name = "api:pesc:bill"

    async def get(
        self, request: web.Request, entry_id: str, account_id: str, bill_id: str
    ) -> web.Response:
        hass = request.app[KEY_HASS]
        coordinator: PescDataUpdateCoordinator | None = hass.data.get(
            const.DOMAIN, {}
        ).get(entry_id)
        if (
            coordinator is None
            or not (account_id.isascii() and account_id.isdigit())
            or _account(coordinator, int(account_id)) is None
            or not _valid_bill_id(bill_id)
        ):
            return web.Response(status=HTTPStatus.NOT_FOUND)

        try:
            async with asyncio.timeout(TIMEOUT):
                body = await coordinator.async_with_relogin(
                    lambda: coordinator.api.client.async_bill_file(
                        int(account_id), bill_id
                    )
                )
        except pesc_client.ClientError as err:
            if isinstance(err, pesc_client.ClientAuthError):
                coordinator.config_entry.async_start_reauth(hass)
            _LOGGER.error("Ошибка загрузки квитанции: %s", err)
            return web.Response(status=HTTPStatus.BAD_GATEWAY, text=err.message)
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.error("Ошибка соединения при загрузке квитанции: %s", err)
            return web.Response(status=HTTPStatus.BAD_GATEWAY, text=str(err))

        return web.Response(
            body=body,
            content_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{bill_id}.pdf"'},
        )
