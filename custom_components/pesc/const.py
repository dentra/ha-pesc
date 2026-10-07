"""Constants for the integration."""

import datetime
from enum import IntFlag
from typing import Final

DOMAIN: Final = "pesc"
DEFAULT_NAME: Final = "ПетроЭлектроСбыт"
CONFIG_VERSION: Final = 2
SERVICE_UPDATE_VALUE = "update_value"
SERVICE_SEND_LINKED: Final = "send_linked"

CONF_AUTH: Final = "auth"
CONF_LOGIN_TYPE: Final = "login_type"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_VALUE: Final = "value"
CONF_VALUES: Final = "values"
CONF_SCALE_ID: Final = "scale_id"
CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_DIAGNOSTIC_SENSORS: Final = "diagnostic_sensors"
CONF_RATES_SENSORS: Final = "rates_sensors"
CONF_LINKS: Final = "links"
CONF_SOURCE: Final = "source"

DEFAULT_UPDATE_INTERVAL: Final = datetime.timedelta(hours=12)

CURRENCY_RUB: Final = "RUB"

MESSAGE_SUCCESS: Final = "Операция выполнена успешно"


class PescEntityFeature(IntFlag):
    MANUAL = 1
