"""Config flow for integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Dict, Optional

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


def _marker(
    marker: vol.Marker, key: str, options: Dict[str, Any], default: Any | None = None
):
    # if default is None:
    #     return marker(key)

    if isinstance(options, dict) and key in options:
        suggested_value = options[key]
    else:
        suggested_value = default

    return marker(key, description={"suggested_value": suggested_value})


def required(
    key: str, options: Dict[str, Any], default: Any | None = None
) -> vol.Required:
    """Return vol.Required."""
    return _marker(vol.Required, key, options, default)


def optional(
    key: str, options: Dict[str, Any], default: Any | None = None
) -> vol.Optional:
    """Return vol.Required."""
    return _marker(vol.Optional, key, options, default)


class ConfigFlowHandler(config_entries.ConfigFlow, domain=const.DOMAIN):
    """Handle a config flow for integration."""

    VERSION = const.CONFIG_VERSION
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    _api: Optional[pesc_api.PescApi] = None
    _reauth_entry: Optional[config_entries.ConfigEntry] = None

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
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm reauth dialog."""
        _LOGGER.debug("async_step_reauth_confirm %s", user_input)

        errors = {}
        assert self._reauth_entry

        if user_input is not None:
            data = {}
            try:
                user_input = {**self._reauth_entry.data, **user_input}
                auth = await self.api.async_relogin(
                    username=user_input[const.CONF_USERNAME],
                    password=user_input[const.CONF_PASSWORD],
                    login_type=self._reauth_entry.data[const.CONF_LOGIN_TYPE],
                )
                data[const.CONF_AUTH] = auth
                _LOGGER.debug("new auth is %s", auth)
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data=self._reauth_entry.data | data,
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            except ConfigFlowError as err:
                errors[err.error_field] = err.error_code
            except pesc_client.ClientAuthError:
                errors[const.CONF_PASSWORD] = "invalid_auth"
            except pesc_client.ClientError as err:
                errors["base"] = "api_error"
                _LOGGER.warning(
                    "Request %s : code=%s, json=%s",
                    err.request_info.url,
                    err.code,
                    err.json,
                )

        return self.async_show_form(
            description_placeholders={
                const.CONF_USERNAME: self._reauth_entry.data[const.CONF_USERNAME]
            },
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    required(const.CONF_PASSWORD, user_input): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD,
                            autocomplete="current-password",
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        """Handle the initial step."""
        if user_input is not None:
            self.context[const.CONF_LOGIN_TYPE] = user_input[const.CONF_LOGIN_TYPE]
            return await self.async_step_auth()

        schema = {
            vol.Required(const.CONF_LOGIN_TYPE, default="phone"): vol.In(
                {
                    "phone": "Номер телефона",
                    "email": "Электронная почта",
                }
            ),
        }

        return self.async_show_form(step_id="user", data_schema=vol.Schema(schema))

    async def async_step_auth(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}

        login_type: str = self.context[const.CONF_LOGIN_TYPE]

        if user_input is not None:
            username: str = user_input.get(login_type, "")
            password: str = user_input.get(const.CONF_PASSWORD, "")
            username = username.replace(" ", "")
            if login_type == "phone":
                if username[0] == "8":
                    username = f"+7{username[1:]}"
                username = username.replace("-", "")
                username = username.replace("(", "")
                username = username.replace(")", "")

            try:
                if login_type == "phone" and (
                    username[0] != "+" or len(username) != len("+71234567890")
                ):
                    raise ConfigFlowError("invalid_username", const.CONF_USERNAME)

                if login_type == "email" and (
                    username.find("@") == -1 or len(username) < len("a@b.cd")
                ):
                    raise ConfigFlowError("invalid_username", const.CONF_USERNAME)

                self._async_abort_entries_match({const.CONF_USERNAME: username})

                if len(password) < 3:
                    raise ConfigFlowError("invalid_password", const.CONF_PASSWORD)

                profile_id = username[1:] if login_type == "phone" else username
                await self.async_set_unique_id(f"{const.DOMAIN}_{slugify(profile_id)}")
                self._abort_if_unique_id_configured()

                self.context[const.CONF_AUTH] = await self.api.async_login(
                    username, password, login_type
                )

                self.context[const.CONF_USERNAME] = username
                if user_input.get(const.CONF_SAVE_PWD, True):
                    self.context[const.CONF_PASSWORD] = password
                return await self.async_step_send_code()

            except ConfigFlowError as err:
                errors[err.error_field] = err.error_code
            except pesc_client.ClientAuthError:
                errors["base"] = "invalid_auth"
            except pesc_client.ClientError as err:
                if err.message:
                    errors["base"] = err.message
                else:
                    errors["base"] = "api_error"
                _LOGGER.warning(
                    "Request %s : code=%s, json=%s",
                    err.request_info.url,
                    err.code,
                    err.json,
                )

            user_input[login_type] = username
            user_input[const.CONF_PASSWORD] = password

        schema = {
            required(login_type, user_input): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.TEL
                    if login_type == "phone"
                    else selector.TextSelectorType.EMAIL,
                    autocomplete="tel" if login_type == "phone" else "email",
                )
            ),
            required(const.CONF_PASSWORD, user_input): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD,
                    autocomplete="current-password",
                )
            ),
            vol.Optional(
                const.CONF_SAVE_PWD,
                default=(user_input or {}).get(const.CONF_SAVE_PWD, True),
            ): selector.BooleanSelector(selector.BooleanSelectorConfig()),
        }

        return self.async_show_form(
            step_id="auth", data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_send_code(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}
        if user_input is not None:
            try:
                verify_type = user_input["verify_type"]
                await self.api.async_confirmation_send(
                    auth=self.context[const.CONF_AUTH], confirmation_type=verify_type
                )
                self.context["verify_type"] = verify_type
                return await self.async_step_verify_code()
            except ConfigFlowError as err:
                errors[err.error_field] = err.error_code
            except pesc_client.ClientAuthError:
                errors["base"] = "invalid_auth"
            except pesc_client.ClientError as err:
                if err.message:
                    errors["base"] = err.message
                else:
                    errors["base"] = "api_error"
                _LOGGER.warning(
                    "Request %s : code=%s, json=%s",
                    err.request_info.url,
                    err.code,
                    err.json,
                )
        types = {}
        _LOGGER.debug("step_send_code auth: %s", self.context[const.CONF_AUTH])
        for typ in self.context[const.CONF_AUTH]["types"]:
            if typ == "PHONE":
                lab = "SMS"
            elif typ == "EMAIL":
                lab = "электронной почте"
            elif typ == "FLASHCALL":
                lab = "звонку"
            else:
                lab = typ
            types[typ] = f"По {lab}"

        schema = {vol.Required("verify_type", default="PHONE"): vol.In(types)}

        return self.async_show_form(
            step_id="send_code", data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_verify_code(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}
        if user_input is not None:
            data = {}
            try:
                data[const.CONF_AUTH] = await self.api.async_confirmation_verify(
                    auth=self.context[const.CONF_AUTH],
                    confirmation_type=self.context["verify_type"],
                    code=user_input["verify_code"],
                )
                data[const.CONF_LOGIN_TYPE] = self.context[const.CONF_LOGIN_TYPE]
                data[const.CONF_USERNAME] = self.context[const.CONF_USERNAME]
                if const.CONF_PASSWORD in self.context:
                    data[const.CONF_PASSWORD] = self.context[const.CONF_PASSWORD]
                await self.api.async_fetch_profile()
                return self.async_create_entry(title=self.api.profile_name, data=data)
            except ConfigFlowError as err:
                errors[err.error_field] = err.error_code
            except pesc_client.ClientAuthError:
                errors["base"] = "invalid_auth"
            except pesc_client.ClientError as err:
                if err.message:
                    errors["base"] = err.message
                else:
                    errors["base"] = "api_error"
                _LOGGER.warning(
                    "Request %s : code=%s, json=%s",
                    err.request_info.url,
                    err.code,
                    err.json,
                )

        schema = {vol.Required("verify_code"): str}
        return self.async_show_form(
            step_id="verify_code",
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
