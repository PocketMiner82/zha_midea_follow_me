"""Resolve the IR blaster that should receive the Midea Follow Me command.

Home Assistant services can target a device in a number of ways. The fields
used here are the standard target fields, so the same service call works with
an entity, a device, an area or a raw ZHA IEEE address.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_IEEE, ZHA_DOMAIN
from .zha_device import is_ir_blaster

_LOGGER = logging.getLogger(__name__)


def get_zha_ieee(device_entry: dr.DeviceEntry | None) -> str | None:
    """Return the ZHA IEEE address of a device registry entry."""
    if device_entry is None:
        return None
    for domain, identifier in device_entry.identifiers:
        if domain == ZHA_DOMAIN:
            return identifier
    return None


def get_device_entry(hass: HomeAssistant, device_id: str) -> dr.DeviceEntry | None:
    """Return a device registry entry by its device id."""
    device_registry = hass.data.get(dr.DATA_REGISTRY)
    if device_registry is None:
        _LOGGER.error("Device registry is not available")
        return None
    return device_registry.async_get(device_id)


def ieee_from_device_id(hass: HomeAssistant, device_id: str) -> str | None:
    """Return the ZHA IEEE address of the given device id."""
    return get_zha_ieee(get_device_entry(hass, device_id))


def ieee_from_entity_id(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return the ZHA IEEE address of the device owning the given entity."""
    entity_registry = hass.data.get(er.DATA_REGISTRY)
    if entity_registry is None:
        _LOGGER.error("Entity registry is not available")
        return None

    entity_entry = entity_registry.async_get(entity_id)
    if entity_entry is None:
        _LOGGER.error("Entity not found: %s", entity_id)
        return None
    if not entity_entry.device_id:
        _LOGGER.error("Entity has no device: %s", entity_id)
        return None

    return ieee_from_device_id(hass, entity_entry.device_id)


def ieee_from_area_id(hass: HomeAssistant, area_id: str) -> str | None:
    """Return the ZHA IEEE address of the IR blaster in the given area.

    A device that exposes the Zosung IR Control cluster is preferred, so an
    area that holds more than one ZHA device still resolves to the blaster.
    """
    area_registry = hass.data.get(ar.DATA_REGISTRY)
    if area_registry is None:
        _LOGGER.error("Area registry is not available")
        return None

    area_entry = area_registry.async_get(area_id)
    if area_entry is None:
        _LOGGER.error("Area not found: %s", area_id)
        return None

    fallback: str | None = None
    for device_id in area_entry.device_id:
        ieee = ieee_from_device_id(hass, device_id)
        if ieee is None:
            continue
        if is_ir_blaster(hass, ieee):
            return ieee
        if fallback is None:
            fallback = ieee

    return fallback


def resolve_ieee(hass: HomeAssistant, data: dict[str, Any]) -> str | None:
    """Resolve the IEEE address of the target described by service data.

    An explicit IEEE address takes precedence, followed by the standard
    device, entity and area target fields.
    """
    if ieee := data.get(CONF_IEEE):
        return ieee

    for device_id in cv.ensure_list(data.get(ATTR_DEVICE_ID)):
        if ieee := ieee_from_device_id(hass, device_id):
            return ieee

    for entity_id in cv.ensure_list(data.get(ATTR_ENTITY_ID)):
        if ieee := ieee_from_entity_id(hass, entity_id):
            return ieee

    for area_id in cv.ensure_list(data.get(ATTR_AREA_ID)):
        if ieee := ieee_from_area_id(hass, area_id):
            return ieee

    return None
