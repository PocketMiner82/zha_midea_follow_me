"""The ZHA Midea Follow Me integration."""
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_IEEE,
    DATA_CONFIG_ENTRIES,
    DATA_TARGET,
    DOMAIN,
    ZHA_DOMAIN,
)
from .services import (
    TARGET_FIELDS,
    async_register_services,
    async_unregister_services,
)
from .targets import resolve_ieee

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                **TARGET_FIELDS,
                vol.Optional(CONF_IEEE): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the ZHA Midea Follow Me integration."""
    hass.data.setdefault(DOMAIN, {})
    yaml_config = dict(config.get(DOMAIN, {}) or {})
    hass.data[DOMAIN][DATA_TARGET] = resolve_ieee(hass, yaml_config)
    await async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ZHA Midea Follow Me from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    configured = hass.data[DOMAIN].setdefault(DATA_CONFIG_ENTRIES, {})
    configured[entry.entry_id] = entry.data.get(CONF_IEEE)

    _async_link_zha_device(hass, entry)

    await async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data = hass.data.get(DOMAIN, {})
    configured = data.get(DATA_CONFIG_ENTRIES, {})
    configured.pop(entry.entry_id, None)

    if not configured and not data.get(DATA_TARGET):
        await async_unregister_services(hass)

    return True


def _async_link_zha_device(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Link the ZHA device of the config entry to the device registry."""
    ieee = entry.data.get(CONF_IEEE)
    if not ieee:
        return

    device_registry = hass.data.get(dr.DATA_REGISTRY)
    if device_registry is None:
        _LOGGER.debug("Device registry is not available")
        return

    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(ZHA_DOMAIN, ieee)},
    )
