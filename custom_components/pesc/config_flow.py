"""Config flow for integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Dict, Final, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowError, FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.schema_config_entry_flow import (
    SchemaConfigFlowHandler,
    SchemaFlowFormStep,
    SchemaFlowMenuStep,
    SchemaOptionsFlowHandler,
)
from homeassistant.util import slugify

from . import const, pesc_api, pesc_client

_LOGGER = logging.getLogger(__name__)

_AUTH: Final = const.CONF_AUTH
_USERNAME: Final = const.CONF_USERNAME
_PASSWORD: Final = const.CONF_PASSWORD
_LOGIN_TYPE: Final = const.CONF_LOGIN_TYPE
_AUTH_TRANSACTION: Final = "auth_transaction"
_SAVE_PWD: Final = "save_password"
_VERIFY_CODE: Final = "verify_code"
_VERIFY_TYPE: Final = "verify_type"
_LOGIN_TYPE_PHONE: Final = pesc_client.LOGIN_TYPE_PHONE
_LOGIN_TYPE_EMAIL: Final = pesc_client.LOGIN_TYPE_EMAIL

_STEP_REAUTH_CONFIRM: Final = "reauth_confirm"
_STEP_USER: Final = "user"
_STEP_AUTH: Final = "auth"
_STEP_SEND_CODE: Final = "send_code"
_STEP_VERIFY_CODE: Final = "verify_code"

_FLOW_ERROR_INVALID_USERNAME: Final = "invalid_username"
_FLOW_ERROR_INVALID_PASSWORD: Final = "invalid_password"

_AUTOCOMPLETE_TEL: Final = "tel"
_AUTOCOMPLETE_EMAIL: Final = "email"
_AUTOCOMPLETE_PASSWORD: Final = "current-password"


class ConfigFlowHandler(config_entries.ConfigFlow, domain=const.DOMAIN):
    """Handle a config flow for integration."""

    VERSION = const.CONFIG_VERSION
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    _api: Optional[pesc_api.PescApi] = None

    @property
    def api(self):
        if self._api is None:
            self._api = pesc_api.PescApi(
                pesc_client.PescClient(async_get_clientsession(self.hass), None)
            )
        return self._api

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return SchemaOptionsFlowHandler(config_entry, OPTIONS_FLOW)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        self.context[_LOGIN_TYPE] = entry_data[_LOGIN_TYPE]
        self.context[_AUTH] = entry_data[_AUTH]
        self.context[_USERNAME] = entry_data[_USERNAME]
        self.context[_PASSWORD] = entry_data.get(_PASSWORD)
        return await self.async_step_reauth_confirm()

    async def _reauth_finish(self, auth: pesc_client.UserAuth) -> FlowResult:
        _LOGGER.debug("new auth is %s", auth)
        reauth_entry = self._get_reauth_entry()
        self.hass.config_entries.async_update_entry(
            reauth_entry,
            data=reauth_entry.data | {_AUTH: auth},
        )
        await self.hass.config_entries.async_reload(self._reauth_entry_id)
        return self.async_abort(reason="reauth_successful")

    async def async_step_reauth_confirm(
        self, user_input: Optional[dict[str, Any]] = None
    ) -> FlowResult:
        """Confirm reauth dialog."""

        _LOGGER.debug("async_step_reauth_confirm %s", user_input)

        errors: Dict[str, str] = {}

        if user_input is not None:
            try:
                if self.api.can_reauth(self.context[_AUTH]):
                    auth = await self.api.async_relogin(
                        username=self.context[_USERNAME],
                        password=user_input[_PASSWORD],
                        auth=self.context[_AUTH],
                        login_type=self.context[_LOGIN_TYPE],
                    )
                    return await self._reauth_finish(auth)

                auth_transaction = await self.api.async_login(
                    username=self.context[_USERNAME],
                    password=user_input[_PASSWORD],
                    login_type=self.context[_LOGIN_TYPE],
                )
                self.context[_AUTH_TRANSACTION] = auth_transaction
                return await self.async_step_send_code()
            except ConfigFlowError as err:
                errors[err.error_field] = err.error_code
            except pesc_client.ClientError as err:
                errors["base"] = str(err)
        else:
            user_input = {_PASSWORD: self.context[_PASSWORD]}

        schema = {
            vol.Required(_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD,
                    autocomplete=_AUTOCOMPLETE_PASSWORD,
                )
            )
        }

        return self.async_show_form(
            description_placeholders={_USERNAME: self.context[_USERNAME]},
            step_id=_STEP_REAUTH_CONFIRM,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), user_input
            ),
            errors=errors,
        )

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        """Handle the initial step."""
        if user_input is not None:
            self.context[_LOGIN_TYPE] = user_input[_LOGIN_TYPE]
            return await self.async_step_auth()

        schema = {
            vol.Required(_LOGIN_TYPE, default=_LOGIN_TYPE_PHONE): vol.In(
                {
                    _LOGIN_TYPE_PHONE: "Номер телефона",
                    _LOGIN_TYPE_EMAIL: "Электронная почта",
                }
            ),
        }

        return self.async_show_form(step_id=_STEP_USER, data_schema=vol.Schema(schema))

    async def async_step_auth(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}

        login_type: str = self.context[_LOGIN_TYPE]

        if user_input is not None:
            password: str = user_input.get(_PASSWORD, "")
            username: str = user_input.get(_USERNAME, "")
            username = username.replace(" ", "")
            if login_type == _LOGIN_TYPE_PHONE:
                if username[0] == "8":
                    username = f"+7{username[1:]}"
                username = username.replace("-", "")
                username = username.replace("(", "")
                username = username.replace(")", "")

            try:
                if login_type == _LOGIN_TYPE_PHONE and (
                    username[0] != "+" or len(username) != len("+71234567890")
                ):
                    raise ConfigFlowError(_FLOW_ERROR_INVALID_USERNAME, _USERNAME)

                if login_type == _LOGIN_TYPE_EMAIL and (
                    username.find("@") == -1 or len(username) < len("a@b.cd")
                ):
                    raise ConfigFlowError(_FLOW_ERROR_INVALID_USERNAME, _USERNAME)

                self._async_abort_entries_match({_USERNAME: username})

                if len(password) < 3:
                    raise ConfigFlowError(_FLOW_ERROR_INVALID_PASSWORD, _PASSWORD)

                profile_id = (
                    username[1:] if login_type == _LOGIN_TYPE_PHONE else username
                )
                await self.async_set_unique_id(f"{const.DOMAIN}_{slugify(profile_id)}")
                self._abort_if_unique_id_configured()

                self.context[_AUTH_TRANSACTION] = await self.api.async_login(
                    username, password, login_type
                )

                self.context[_USERNAME] = username
                if user_input.get(_SAVE_PWD, True):
                    self.context[_PASSWORD] = password
                return await self.async_step_send_code()

            except ConfigFlowError as err:
                errors[err.error_field] = err.error_code
            except pesc_client.ClientError as err:
                errors["base"] = str(err)

            user_input[_USERNAME] = username
            user_input[_PASSWORD] = password

        schema = {
            vol.Required(_USERNAME): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.TEL
                    if login_type == _LOGIN_TYPE_PHONE
                    else selector.TextSelectorType.EMAIL,
                    autocomplete=_AUTOCOMPLETE_TEL
                    if login_type == _LOGIN_TYPE_PHONE
                    else _AUTOCOMPLETE_EMAIL,
                )
            ),
            vol.Required(_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD,
                    autocomplete=_AUTOCOMPLETE_PASSWORD,
                )
            ),
            vol.Optional(
                _SAVE_PWD,
                default=(user_input or {}).get(_SAVE_PWD, True),
            ): selector.BooleanSelector(selector.BooleanSelectorConfig()),
        }

        return self.async_show_form(
            step_id=_STEP_AUTH,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), user_input
            ),
            errors=errors,
        )

    async def async_step_send_code(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}
        if user_input is not None:
            try:
                verify_type = user_input[_VERIFY_TYPE]
                auth_transaction = self.context[_AUTH_TRANSACTION]
                auth_transaction = await self.api.async_login_confirmation_send(
                    auth_transaction=auth_transaction, confirmation_type=verify_type
                )
                self.context[_AUTH_TRANSACTION] = auth_transaction
                return await self.async_step_verify_code()
            except ConfigFlowError as err:
                errors[err.error_field] = err.error_code
            except pesc_client.ClientError as err:
                errors["base"] = str(err)

        types = {}
        _LOGGER.debug("step_send_code auth: %s", self.context[_AUTH_TRANSACTION])
        for typ in self.context[_AUTH_TRANSACTION]["types"]:
            if typ == pesc_client.CONFIRMATION_SMS:
                lab = "SMS"
            elif typ == pesc_client.CONFIRMATION_EMAIL:
                lab = "электронной почте"
            elif typ == pesc_client.CONFIRMATION_CALL:
                lab = "звонку"
            else:
                lab = typ
            types[typ] = f"По {lab}"

        schema = {
            vol.Required(_VERIFY_TYPE, default=pesc_client.CONFIRMATION_SMS): vol.In(
                types
            )
        }

        return self.async_show_form(
            step_id=_STEP_SEND_CODE, data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_verify_code(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}
        if user_input is not None:
            try:
                auth = await self.api.async_login_confirmation_verify(
                    auth_transaction=self.context[_AUTH_TRANSACTION],
                    code=user_input[_VERIFY_CODE],
                )

                if self.source == config_entries.SOURCE_REAUTH:
                    return await self._reauth_finish(auth)

                await self.api.async_fetch_profile()

                data = {
                    _AUTH: auth,
                    _LOGIN_TYPE: self.context[_LOGIN_TYPE],
                    _USERNAME: self.context[_USERNAME],
                }
                if _PASSWORD in self.context:
                    data[_PASSWORD] = self.context[_PASSWORD]
                return self.async_create_entry(title=self.api.profile_name, data=data)
            except ConfigFlowError as err:
                errors[err.error_field] = err.error_code
            except pesc_client.ClientError as err:
                errors["base"] = str(err)

        schema = {vol.Required(_VERIFY_CODE): str}
        return self.async_show_form(
            step_id=_STEP_VERIFY_CODE,
            data_schema=vol.Schema(schema),
            errors=errors,
            last_step=True,
        )


async def general_options_schema(
    handler: SchemaConfigFlowHandler | SchemaOptionsFlowHandler,
) -> vol.Schema:
    def timedelta_to_dict(delta: timedelta) -> dict:
        hours, seconds = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return {
            "days": delta.days,
            "hours": hours,
            "minutes": minutes,
            "seconds": seconds,
        }

    return vol.Schema(
        {
            vol.Optional(
                const.CONF_UPDATE_INTERVAL,
                default=timedelta_to_dict(
                    cv.time_period(
                        handler.options.get(
                            const.CONF_UPDATE_INTERVAL,
                            const.DEFAULT_UPDATE_INTERVAL.total_seconds(),
                        )
                    )
                ),
            ): selector.DurationSelector(
                selector.DurationSelectorConfig(enable_day=True),
            ),
            vol.Optional(
                const.CONF_RATES_SENSORS,
                default=handler.options.get(const.CONF_RATES_SENSORS, True),
            ): selector.BooleanSelector(selector.BooleanSelectorConfig()),
            vol.Optional(
                const.CONF_DIAGNOSTIC_SENSORS,
                default=handler.options.get(const.CONF_DIAGNOSTIC_SENSORS, False),
            ): selector.BooleanSelector(selector.BooleanSelectorConfig()),
        }
    )


OPTIONS_FLOW: Dict[str, SchemaFlowFormStep | SchemaFlowMenuStep] = {
    "init": SchemaFlowFormStep(general_options_schema),
}


class ConfigFlowError(FlowError):
    def __init__(self, error_code: str, error_field: str = "base") -> None:
        super().__init__(self.__class__.__name__)
        self.error_code = error_code
        self.error_field = error_field
