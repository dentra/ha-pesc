import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from .conftest import mock_data


async def _setup(hass, aioclient_mock, config_entry) -> None:
    config_entry.add_to_hass(hass)
    mock_data(aioclient_mock)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_meter_sensors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)

    day = hass.states.get("sensor.pesc_00000abc12_2")
    assert float(day.state) == 12345
    assert day.attributes["unit_of_measurement"] == "kWh"
    assert day.attributes["scale_id"] == 2
    assert hass.states.get("sensor.pesc_00000abc12_3_rate") is not None


async def test_valid_entity_id(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _setup(hass, aioclient_mock, config_entry)

    assert "invalid entity ID" not in caplog.text
