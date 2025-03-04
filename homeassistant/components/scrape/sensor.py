"""Support for getting data from websites with scraping."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.rest import RESOURCE_SCHEMA, create_rest_data_from_config
from homeassistant.components.sensor import (
    CONF_STATE_CLASS,
    DEVICE_CLASSES_SCHEMA,
    PLATFORM_SCHEMA as PARENT_PLATFORM_SCHEMA,
    STATE_CLASSES_SCHEMA,
    SensorEntity,
)
from homeassistant.const import (
    CONF_ATTRIBUTE,
    CONF_AUTHENTICATION,
    CONF_DEVICE_CLASS,
    CONF_HEADERS,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_RESOURCE,
    CONF_UNIQUE_ID,
    CONF_UNIT_OF_MEASUREMENT,
    CONF_USERNAME,
    CONF_VALUE_TEMPLATE,
    CONF_VERIFY_SSL,
    HTTP_BASIC_AUTHENTICATION,
    HTTP_DIGEST_AUTHENTICATION,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import PlatformNotReady
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.template import Template
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_INDEX, CONF_SELECT, DEFAULT_NAME, DEFAULT_VERIFY_SSL
from .coordinator import ScrapeCoordinator

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=10)

PLATFORM_SCHEMA = PARENT_PLATFORM_SCHEMA.extend(
    {
        # Linked to the loading of the page (can be linked to RestData)
        vol.Optional(CONF_AUTHENTICATION): vol.In(
            [HTTP_BASIC_AUTHENTICATION, HTTP_DIGEST_AUTHENTICATION]
        ),
        vol.Optional(CONF_HEADERS): vol.Schema({cv.string: cv.string}),
        vol.Optional(CONF_PASSWORD): cv.string,
        vol.Required(CONF_RESOURCE): cv.string,
        vol.Optional(CONF_USERNAME): cv.string,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
        # Linked to the parsing of the page (specific to scrape)
        vol.Optional(CONF_ATTRIBUTE): cv.string,
        vol.Optional(CONF_INDEX, default=0): cv.positive_int,
        vol.Required(CONF_SELECT): cv.string,
        vol.Optional(CONF_VALUE_TEMPLATE): cv.template,
        # Linked to the sensor definition (can be linked to TemplateSensor)
        vol.Optional(CONF_DEVICE_CLASS): DEVICE_CLASSES_SCHEMA,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_STATE_CLASS): STATE_CLASSES_SCHEMA,
        vol.Optional(CONF_UNIQUE_ID): cv.string,
        vol.Optional(CONF_UNIT_OF_MEASUREMENT): cv.string,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Web scrape sensor."""
    resource_config = vol.Schema(RESOURCE_SCHEMA, extra=vol.REMOVE_EXTRA)(config)
    rest = create_rest_data_from_config(hass, resource_config)

    coordinator = ScrapeCoordinator(hass, rest, SCAN_INTERVAL)
    await coordinator.async_refresh()
    if coordinator.data is None:
        raise PlatformNotReady

    name: str = config[CONF_NAME]
    select: str | None = config.get(CONF_SELECT)
    attr: str | None = config.get(CONF_ATTRIBUTE)
    index: int = config[CONF_INDEX]
    unit: str | None = config.get(CONF_UNIT_OF_MEASUREMENT)
    device_class: str | None = config.get(CONF_DEVICE_CLASS)
    state_class: str | None = config.get(CONF_STATE_CLASS)
    unique_id: str | None = config.get(CONF_UNIQUE_ID)
    value_template: Template | None = config.get(CONF_VALUE_TEMPLATE)

    if value_template is not None:
        value_template.hass = hass

    async_add_entities(
        [
            ScrapeSensor(
                coordinator,
                unique_id,
                name,
                select,
                attr,
                index,
                value_template,
                unit,
                device_class,
                state_class,
            )
        ],
    )


class ScrapeSensor(CoordinatorEntity[ScrapeCoordinator], SensorEntity):
    """Representation of a web scrape sensor."""

    def __init__(
        self,
        coordinator: ScrapeCoordinator,
        unique_id: str | None,
        name: str,
        select: str | None,
        attr: str | None,
        index: int,
        value_template: Template | None,
        unit: str | None,
        device_class: str | None,
        state_class: str | None,
    ) -> None:
        """Initialize a web scrape sensor."""
        super().__init__(coordinator)
        self._attr_native_value = None
        self._select = select
        self._attr = attr
        self._index = index
        self._value_template = value_template
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class

    def _extract_value(self) -> Any:
        """Parse the html extraction in the executor."""
        raw_data = self.coordinator.data
        _LOGGER.debug("Raw beautiful soup: %s", raw_data)
        try:
            if self._attr is not None:
                value = raw_data.select(self._select)[self._index][self._attr]
            else:
                tag = raw_data.select(self._select)[self._index]
                if tag.name in ("style", "script", "template"):
                    value = tag.string
                else:
                    value = tag.text
        except IndexError:
            _LOGGER.warning("Index '%s' not found in %s", self._index, self.entity_id)
            value = None
        except KeyError:
            _LOGGER.warning(
                "Attribute '%s' not found in %s", self._attr, self.entity_id
            )
            value = None
        _LOGGER.debug("Parsed value: %s", value)
        return value

    async def async_added_to_hass(self) -> None:
        """Ensure the data from the initial update is reflected in the state."""
        await super().async_added_to_hass()
        self._async_update_from_rest_data()

    def _async_update_from_rest_data(self) -> None:
        """Update state from the rest data."""
        value = self._extract_value()

        if self._value_template is not None:
            self._attr_native_value = (
                self._value_template.async_render_with_possible_json_value(value, None)
            )
        else:
            self._attr_native_value = value

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._async_update_from_rest_data()
        super()._handle_coordinator_update()
