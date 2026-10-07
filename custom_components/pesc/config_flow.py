"""Config flow for integration."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Dict, Final, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowError, FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.schema_config_entry_flow import (
    SchemaConfigFlowHandler,
    SchemaFlowError,
    SchemaFlowFormStep,
    SchemaFlowMenuStep,
    SchemaOptionsFlowHandler,
    SchemaOptionsFlowHandlerWithReload,
)
from homeassistant.util import slugify

from . import const, pesc_api, pesc_client
from .services import linked_preview

_LOGGER = logging.getLogger(__name__)

_AUTH: Final = const.CONF_AUTH
_USERNAME: Final = const.CONF_USERNAME
_PASSWORD: Final = const.CONF_PASSWORD
_LOGIN_TYPE: Final = const.CONF_LOGIN_TYPE
_SAVE_PWD: Final = "save_password"
_VERIFY_CODE: Final = "verify_code"
_TOTP_CODE: Final = "totp_code"
_VERIFY_TYPE: Final = "verify_type"
_LOGIN_TYPE_PHONE: Final = pesc_client.LOGIN_TYPE_PHONE
_LOGIN_TYPE_EMAIL: Final = pesc_client.LOGIN_TYPE_EMAIL

_STEP_REAUTH_CONFIRM: Final = "reauth_confirm"
_STEP_TOTP_CODE: Final = "totp_code"
_STEP_USER: Final = "user"
_STEP_AUTH: Final = "auth"
_STEP_SEND_CODE: Final = "send_code"
_STEP_VERIFY_CODE: Final = "verify_code"

_FLOW_ERROR_INVALID_USERNAME: Final = "invalid_username"
_FLOW_ERROR_INVALID_PASSWORD: Final = "invalid_password"
_FLOW_ERROR_INVALID_TOTP: Final = "invalid_totp"

_SUPPORTED_CONFIRMATION_TYPES: Final = (
    pesc_client.CONFIRMATION_SMS,
    pesc_client.CONFIRMATION_EMAIL,
    pesc_client.CONFIRMATION_CALL,
    pesc_client.CONFIRMATION_TOTP,
)

_AUTOCOMPLETE_TEL: Final = "tel"
_AUTOCOMPLETE_EMAIL: Final = "email"
_AUTOCOMPLETE_PASSWORD: Final = "current-password"


class ConfigFlowHandler(config_entries.ConfigFlow, domain=const.DOMAIN):
    """Handle a config flow for integration."""

    VERSION = const.CONFIG_VERSION
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    _api: Optional[pesc_api.PescApi] = None
    # секреты держим вне self.context: его видит фронтенд
    _password: Optional[str] = None
    _auth: Optional[pesc_client.UserAuth] = None
    _auth_transaction: Optional[pesc_client.UserAuthTransaction] = None

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
        return SchemaOptionsFlowHandlerWithReload(config_entry, OPTIONS_FLOW)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        self.context[_LOGIN_TYPE] = entry_data[_LOGIN_TYPE]
        self._auth = entry_data[_AUTH]
        self.context[_USERNAME] = entry_data[_USERNAME]
        self._password = entry_data.get(_PASSWORD)
        return await self.async_step_reauth_confirm()

    async def _reauth_finish(self, auth: pesc_client.UserAuth) -> FlowResult:
        reauth_entry = self._get_reauth_entry()
        # relogin не возвращает verified
        data = reauth_entry.data | {_AUTH: reauth_entry.data[_AUTH] | auth}
        if _PASSWORD in reauth_entry.data and self._password:
            data[_PASSWORD] = self._password
        return self.async_update_reload_and_abort(reauth_entry, data=data)

    async def async_step_reauth_confirm(
        self, user_input: Optional[dict[str, Any]] = None
    ) -> FlowResult:
        """Confirm reauth dialog."""

        errors: Dict[str, str] = {}

        if user_input is not None:
            try:
                self._password = user_input[_PASSWORD]
                if self.api.can_reauth(self._auth):
                    auth = await self.api.async_relogin(
                        username=self.context[_USERNAME],
                        password=user_input[_PASSWORD],
                        auth=self._auth,
                        login_type=self.context[_LOGIN_TYPE],
                    )
                    return await self._reauth_finish(auth)

                auth_transaction = await self.api.async_login(
                    username=self.context[_USERNAME],
                    password=user_input[_PASSWORD],
                    login_type=self.context[_LOGIN_TYPE],
                )
                self._auth_transaction = auth_transaction
                return await self.async_step_send_code()
            except pesc_client.ClientTwoFactorRequired as err:
                if err.transaction_id:
                    self._auth_transaction = {
                        "transactionId": err.transaction_id,
                        "types": err.types,
                    }
                    return await self.async_step_send_code()
                errors["base"] = str(err)
            except ConfigFlowError as err:
                errors[err.error_field] = err.error_code
            except pesc_client.ClientError as err:
                errors["base"] = str(err)
        else:
            user_input = {_PASSWORD: self._password}

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

    async def async_step_totp_code(
        self, user_input: Optional[dict[str, Any]] = None
    ) -> FlowResult:
        """Handle a TOTP code for the authentication transaction."""

        errors: Dict[str, str] = {}

        if user_input is not None:
            code = re.sub(r"[\s-]", "", str(user_input.get(_TOTP_CODE, "")))
            if not 6 <= len(code) <= 8 or not code.isdigit():
                errors[_TOTP_CODE] = _FLOW_ERROR_INVALID_TOTP
            else:
                try:
                    auth = await self.api.async_login_totp_confirmation_verify(
                        transaction_id=self._auth_transaction["transactionId"],
                        code=code,
                    )
                    return await self._finish_auth(auth)
                except pesc_client.ClientError as err:
                    if str(err.code) in {"400", "1024"}:
                        errors[_TOTP_CODE] = _FLOW_ERROR_INVALID_TOTP
                    else:
                        errors["base"] = str(err)

            user_input[_TOTP_CODE] = code

        schema = {
            vol.Required(_TOTP_CODE): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.TEXT,
                    autocomplete="one-time-code",
                )
            )
        }
        return self.async_show_form(
            step_id=_STEP_TOTP_CODE,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), user_input
            ),
            errors=errors,
            last_step=True,
        )

    async def _finish_auth(self, auth: pesc_client.UserAuth) -> FlowResult:
        if self.source == config_entries.SOURCE_REAUTH:
            return await self._reauth_finish(auth)

        await self.api.async_fetch_profile()
        data = {
            _AUTH: auth,
            _LOGIN_TYPE: self.context[_LOGIN_TYPE],
            _USERNAME: self.context[_USERNAME],
        }
        if self._password is not None:
            data[_PASSWORD] = self._password
        return self.async_create_entry(title=self.api.profile_name, data=data)

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        """Handle the initial step."""
        if user_input is not None:
            self.context[_LOGIN_TYPE] = user_input[_LOGIN_TYPE]
            return await self.async_step_auth()

        try:
            cfg = await self.api.client.async_config()
            auth_type = cfg["users"]["authentication"]["type"]
            schema = {
                vol.Required(
                    _LOGIN_TYPE, default=auth_type["default"].lower()
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[typ.lower() for typ in auth_type["available"]],
                        translation_key="login_types",
                    )
                )
            }
            return self.async_show_form(
                step_id=_STEP_USER, data_schema=vol.Schema(schema)
            )
        except Exception:
            return self.async_abort(reason="service_unavailable")

    async def async_step_auth(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}

        login_type: str = self.context[_LOGIN_TYPE]

        if user_input is not None:
            password: str = user_input.get(_PASSWORD, "")
            username: str = user_input.get(login_type, "")
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

                self._auth_transaction = await self.api.async_login(
                    username, password, login_type
                )

                self.context[_USERNAME] = username
                if user_input.get(_SAVE_PWD, True):
                    self._password = password
                return await self.async_step_send_code()

            except ConfigFlowError as err:
                errors[err.error_field] = err.error_code
            except pesc_client.ClientError as err:
                errors["base"] = str(err)

            user_input[login_type] = username
            user_input[_PASSWORD] = password

        schema = {
            vol.Required(login_type): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=(
                        selector.TextSelectorType.TEL
                        if login_type == _LOGIN_TYPE_PHONE
                        else selector.TextSelectorType.EMAIL
                    ),
                    autocomplete=(
                        _AUTOCOMPLETE_TEL
                        if login_type == _LOGIN_TYPE_PHONE
                        else _AUTOCOMPLETE_EMAIL
                    ),
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
                verify_type = user_input[_VERIFY_TYPE].upper()
                auth_transaction = self._auth_transaction
                if verify_type == pesc_client.CONFIRMATION_TOTP:
                    return await self.async_step_totp_code()
                auth_transaction = await self.api.async_login_confirmation_send(
                    auth_transaction=auth_transaction, confirmation_type=verify_type
                )
                self._auth_transaction = auth_transaction
                return await self.async_step_verify_code()
            except ConfigFlowError as err:
                errors[err.error_field] = err.error_code
            except pesc_client.ClientError as err:
                errors["base"] = str(err)

        _LOGGER.debug("step_send_code auth: %s", self._auth_transaction)
        received_types = {str(typ).upper() for typ in self._auth_transaction["types"]}
        # ключи вариантов в переводах только строчные
        available_types = [
            typ.lower()
            for typ in _SUPPORTED_CONFIRMATION_TYPES
            if typ in received_types
        ]
        if not available_types:
            return self.async_abort(
                reason="unsupported_verify_types",
                description_placeholders={"types": ", ".join(received_types)},
            )

        schema = {
            vol.Required(
                _VERIFY_TYPE, default=available_types[0]
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=available_types,
                    translation_key="verify_types",
                )
            ),
        }

        return self.async_show_form(
            step_id=_STEP_SEND_CODE, data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_verify_code(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}
        if user_input is not None:
            try:
                auth = await self.api.async_login_confirmation_verify(
                    auth_transaction=self._auth_transaction,
                    code=user_input[_VERIFY_CODE],
                )

                return await self._finish_auth(auth)
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


def _pesc_entities(
    handler: SchemaConfigFlowHandler | SchemaOptionsFlowHandler,
) -> list[er.RegistryEntry]:
    hass = handler.parent_handler.hass
    registry = er.async_get(hass)
    return [
        reg
        for entry in hass.config_entries.async_entries(const.DOMAIN)
        for reg in er.async_entries_for_config_entry(registry, entry.entry_id)
    ]


def _manual_meters(
    handler: SchemaConfigFlowHandler | SchemaOptionsFlowHandler,
) -> list[str]:
    registry = er.async_get(handler.parent_handler.hass)
    entry_id = handler.parent_handler.config_entry.entry_id
    return [
        reg.entity_id
        for reg in er.async_entries_for_config_entry(registry, entry_id)
        if reg.domain == "sensor"
        and not reg.disabled
        and reg.supported_features & const.PescEntityFeature.MANUAL
    ]


def _meter_choices(
    handler: SchemaConfigFlowHandler | SchemaOptionsFlowHandler,
) -> list[str]:
    # сохранённые счётчики, даже удалённые, иначе форму нельзя сохранить
    saved = [link[ATTR_ENTITY_ID] for link in handler.options.get(const.CONF_LINKS, [])]
    return list(dict.fromkeys(_manual_meters(handler) + saved))


async def links_options_schema(
    handler: SchemaConfigFlowHandler | SchemaOptionsFlowHandler,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(const.CONF_LINKS): selector.ObjectSelector(
                selector.ObjectSelectorConfig(
                    multiple=True,
                    label_field=ATTR_ENTITY_ID,
                    description_field=const.CONF_SOURCE,
                    translation_key=const.CONF_LINKS,
                    fields={
                        ATTR_ENTITY_ID: {
                            "required": True,
                            "selector": selector.EntitySelector(
                                selector.EntitySelectorConfig(
                                    include_entities=_meter_choices(handler)
                                )
                            ),
                        },
                        const.CONF_SOURCE: {
                            "required": True,
                            "selector": selector.EntitySelector(
                                selector.EntitySelectorConfig(
                                    domain=["sensor", "input_number"],
                                    exclude_entities=[
                                        reg.entity_id for reg in _pesc_entities(handler)
                                    ],
                                )
                            ),
                        },
                    },
                )
            ),
        }
    )


async def validate_links(
    handler: SchemaConfigFlowHandler | SchemaOptionsFlowHandler,
    user_input: dict[str, Any],
) -> dict[str, Any]:
    meters = [link[ATTR_ENTITY_ID] for link in user_input.get(const.CONF_LINKS, [])]
    if len(meters) != len(set(meters)):
        raise SchemaFlowError("duplicate_link")
    if set(meters) - set(_manual_meters(handler)):
        raise SchemaFlowError("missing_meter")
    return user_input


async def links_preview(
    handler: SchemaConfigFlowHandler | SchemaOptionsFlowHandler,
) -> dict[str, str]:
    entry_id = handler.parent_handler.config_entry.entry_id
    return {"preview": linked_preview(handler.parent_handler.hass, entry_id)}


OPTIONS_FLOW: Dict[str, SchemaFlowFormStep | SchemaFlowMenuStep] = {
    "init": SchemaFlowFormStep(general_options_schema, next_step="links"),
    "links": SchemaFlowFormStep(
        links_options_schema,
        validate_user_input=validate_links,
        description_placeholders=links_preview,
    ),
}


class ConfigFlowError(FlowError):
    def __init__(self, error_code: str, error_field: str = "base") -> None:
        super().__init__(self.__class__.__name__)
        self.error_code = error_code
        self.error_field = error_field
