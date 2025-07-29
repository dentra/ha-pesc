"""Constants for the integration."""

import datetime
from enum import IntFlag
from typing import Final

DOMAIN: Final = "pesc"
DEFAULT_NAME: Final = "ПетроЭлектроСбыт"
CONFIG_VERSION: Final = 1
SERVICE_UPDATE_VALUE = "update_value"

CONF_TOKEN: Final = "token"
CONF_AUTH: Final = "auth"
CONF_LOGIN_TYPE: Final = "login_type"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_SAVE_PWD: Final = "save_password"
CONF_VALUE: Final = "value"
CONF_SCALE_ID: Final = "scale_id"
CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_DIAGNOSTIC_SENSORS: Final = "diagnostic_sensors"
CONF_RATES_SENSORS: Final = "rates_sensors"

DEFAULT_UPDATE_INTERVAL: Final = datetime.timedelta(hours=12)

CURRENCY_RUB: Final = "RUB"


class PescEntityFeature(IntFlag):
    MANUAL = 1
