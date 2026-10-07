import pytest
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.pesc import const

from .conftest import ACCOUNT_ID, API_URL, mock_data

DAY = "sensor.pesc_00000abc12_2"
NIGHT = "sensor.pesc_00000abc12_3"
READING_URL = f"{API_URL}/v7/accounts/{ACCOUNT_ID}/meters/00000ABC12/reading"
LINKS = [
    {"entity_id": DAY, "source": "sensor.src_day"},
    {"entity_id": NIGHT, "source": "sensor.src_night"},
]


async def _setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    options: dict,
) -> None:
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options=options)
    mock_data(aioclient_mock)
    aioclient_mock.post(READING_URL)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


def _button(hass: HomeAssistant) -> str | None:
    return next(
        (
            entry.entity_id
            for entry in er.async_get(hass).entities.values()
            if entry.platform == const.DOMAIN and entry.domain == "button"
        ),
        None,
    )


def _sent(aioclient_mock: AiohttpClientMocker) -> list:
    return [
        call[2]
        for call in aioclient_mock.mock_calls
        if call[0].upper() == "POST" and str(call[1]) == READING_URL
    ]


async def _send_linked(hass: HomeAssistant, **kwargs):
    return await hass.services.async_call(
        const.DOMAIN,
        const.SERVICE_SEND_LINKED,
        {},
        blocking=True,
        return_response=kwargs.pop("return_response", True),
    )


async def test_options_links(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry, {})

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["step_id"] == "links"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {const.CONF_LINKS: LINKS}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[const.CONF_LINKS] == LINKS


async def test_options_links_duplicate(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry, {})

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            const.CONF_LINKS: [
                {"entity_id": DAY, "source": "sensor.src_day"},
                {"entity_id": DAY, "source": "sensor.src_night"},
            ]
        },
    )

    assert result["errors"] == {"base": "duplicate_link"}


@pytest.mark.parametrize(
    "link",
    [
        {"entity_id": f"{DAY}_rate", "source": "sensor.src"},
        {"entity_id": "sensor.other", "source": "sensor.src"},
        {"entity_id": DAY, "source": NIGHT},
    ],
    ids=["rate", "foreign", "pesc_source"],
)
async def test_options_links_not_offered(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    link: dict,
) -> None:
    await _setup(hass, aioclient_mock, config_entry, {})
    hass.states.async_set("sensor.other", "1")

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {const.CONF_LINKS: [link]}
        )


async def test_options_links_cleared(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry, {const.CONF_LINKS: LINKS})

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert const.CONF_LINKS not in config_entry.options
    assert _button(hass) is None


async def test_send_linked(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry, {const.CONF_LINKS: LINKS})
    hass.states.async_set(
        "sensor.src_day", "12350.7", {ATTR_UNIT_OF_MEASUREMENT: "kWh"}
    )
    hass.states.async_set(
        "sensor.src_night", "6790500", {ATTR_UNIT_OF_MEASUREMENT: "Wh"}
    )

    response = await _send_linked(hass)

    assert response["code"] == 0
    assert _sent(aioclient_mock) == [
        [{"scaleId": 2, "value": 12350}, {"scaleId": 3, "value": 6790}]
    ]


async def test_send_linked_unavailable_source(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry, {const.CONF_LINKS: LINKS})
    hass.states.async_set("sensor.src_day", "12350", {ATTR_UNIT_OF_MEASUREMENT: "kWh"})
    hass.states.async_set("sensor.src_night", "unavailable")

    response = await _send_linked(hass)

    codes = {result["entity_ids"][0]: result["code"] for result in response["results"]}
    assert codes == {NIGHT: -4, DAY: 0}
    assert response["code"] == -4
    assert _sent(aioclient_mock) == [
        [{"scaleId": 2, "value": 12350}, {"scaleId": 3, "value": 6789.0}]
    ]


async def test_send_linked_incompatible_unit(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry, {const.CONF_LINKS: LINKS[:1]})
    hass.states.async_set("sensor.src_day", "12350", {ATTR_UNIT_OF_MEASUREMENT: "m³"})

    response = await _send_linked(hass)

    assert response["code"] == -5
    assert not _sent(aioclient_mock)


async def test_send_linked_without_links(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry, {})

    with pytest.raises(ServiceValidationError):
        await _send_linked(hass)


@pytest.mark.parametrize(
    ("options", "exists"),
    [
        ({const.CONF_LINKS: LINKS}, True),
        ({}, False),
    ],
    ids=["links", "no_links"],
)
async def test_button_created(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    options: dict,
    exists: bool,
) -> None:
    await _setup(hass, aioclient_mock, config_entry, options)

    assert (_button(hass) is not None) is exists


async def test_button_press(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(
        hass,
        aioclient_mock,
        config_entry,
        {const.CONF_LINKS: LINKS},
    )
    hass.states.async_set("sensor.src_day", "12350", {ATTR_UNIT_OF_MEASUREMENT: "kWh"})
    hass.states.async_set("sensor.src_night", "6790", {ATTR_UNIT_OF_MEASUREMENT: "kWh"})

    await hass.services.async_call(
        "button", "press", {"entity_id": _button(hass)}, blocking=True
    )

    assert _sent(aioclient_mock) == [
        [{"scaleId": 2, "value": 12350}, {"scaleId": 3, "value": 6790}]
    ]


async def test_button_press_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(
        hass,
        aioclient_mock,
        config_entry,
        {const.CONF_LINKS: LINKS[:1]},
    )
    hass.states.async_set("sensor.src_day", "1", {ATTR_UNIT_OF_MEASUREMENT: "kWh"})

    with pytest.raises(HomeAssistantError, match="меньше предыдущего"):
        await hass.services.async_call(
            "button", "press", {"entity_id": _button(hass)}, blocking=True
        )


async def test_options_links_preview(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry, {const.CONF_LINKS: LINKS})
    hass.states.async_set(
        "sensor.src_day",
        "12350.7",
        {ATTR_UNIT_OF_MEASUREMENT: "kWh", "friendly_name": "Источник день"},
    )
    hass.states.async_set("sensor.src_night", "1", {ATTR_UNIT_OF_MEASUREMENT: "kWh"})

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})

    preview = result["description_placeholders"]["preview"]
    assert "12350 (сейчас 12345) ← Источник день" in preview
    assert "меньше предыдущего" in preview


async def test_options_links_preview_empty(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry, {})

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})

    assert result["description_placeholders"] == {"preview": ""}


async def test_send_linked_conversion_rounding(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry, {const.CONF_LINKS: LINKS[:1]})
    hass.states.async_set("sensor.src_day", "12.357", {ATTR_UNIT_OF_MEASUREMENT: "MWh"})

    await _send_linked(hass)

    assert _sent(aioclient_mock)[0][0] == {"scaleId": 2, "value": 12357}


@pytest.mark.parametrize("state", ["nan", "inf", "0", "-5"])
async def test_send_linked_invalid_source_value(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    state: str,
) -> None:
    await _setup(hass, aioclient_mock, config_entry, {const.CONF_LINKS: LINKS[:1]})
    hass.states.async_set("sensor.src_day", state, {ATTR_UNIT_OF_MEASUREMENT: "kWh"})

    response = await _send_linked(hass)

    assert response["code"] == -4
    assert not _sent(aioclient_mock)


async def test_send_linked_missing_meter(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    links = [{"entity_id": "sensor.pesc_gone_2", "source": "sensor.src_day"}]
    await _setup(hass, aioclient_mock, config_entry, {const.CONF_LINKS: links})

    response = await _send_linked(hass)

    assert [result["code"] for result in response["results"]] == [-6]
    assert "sensor.pesc_gone_2" in response["message"]
