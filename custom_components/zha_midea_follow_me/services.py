"""Service handlers for the ZHA Midea Follow Me integration."""
import base64
import json
import logging
from functools import partial
from typing import Any

import voluptuous as vol

from homeassistant.const import ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_BEEPER,
    CONF_IEEE,
    CONF_TEMPERATURE,
    CONF_USE_FAHRENHEIT,
    DOMAIN,
    SERVICE_DEBUG,
    SERVICE_FOLLOW_ME,
)
from .helpers import get_default_target
from .midea import build_midea_follow_me_data, midea_5bytes_to_raw
from .targets import resolve_ieee
from .tuya_ir import encode_tuya_ir
from .zha_device import async_send_ir_code, get_zha_device

_LOGGER = logging.getLogger(__name__)

# The standard Home Assistant target fields. They are accepted next to the
# other service fields so a service call can target the blaster with an
# entity, a device, an area or a raw IEEE address.
TARGET_FIELDS: dict[vol.Marker, Any] = {
    vol.Optional(ATTR_AREA_ID): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
}

SERVICE_FOLLOW_ME_SCHEMA = vol.Schema(
    {
        **TARGET_FIELDS,
        vol.Optional(CONF_IEEE): cv.string,
        vol.Required(CONF_TEMPERATURE): vol.Coerce(float),
        vol.Optional(CONF_USE_FAHRENHEIT, default=False): cv.boolean,
        vol.Optional(CONF_BEEPER, default=False): cv.boolean,
    }
)

SERVICE_DEBUG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TEMPERATURE): vol.Coerce(float),
        vol.Optional(CONF_USE_FAHRENHEIT, default=False): cv.boolean,
        vol.Optional(CONF_BEEPER, default=False): cv.boolean,
    }
)


async def async_handle_send_follow_me(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the send_follow_me service call."""
    ieee = resolve_ieee(hass, call.data) or get_default_target(hass)
    temperature = call.data[CONF_TEMPERATURE]
    use_fahrenheit = call.data.get(CONF_USE_FAHRENHEIT, False)
    beeper = call.data.get(CONF_BEEPER, False)

    if not ieee:
        _LOGGER.error(
            "No target specified and no blaster configured for %s. "
            "Target the blaster with entity_id, device_id, area_id or ieee, "
            "or add the integration from the UI.",
            DOMAIN,
        )
        return

    temp_symbol = "°F" if use_fahrenheit else "°C"
    _LOGGER.debug(
        "Sending Midea Follow Me command: temp=%s%s, beeper=%s to %s",
        temperature,
        temp_symbol,
        beeper,
        ieee,
    )

    try:
        # Step 1: Build IR data payload
        ir_data = build_midea_follow_me_data(temperature, use_fahrenheit, beeper)
        ir_hex = " ".join(f"{b:02X}" for b in ir_data)
        _LOGGER.debug("IR Data (hex): %s", ir_hex)

        # Step 2: Convert 5 bytes to raw and then to tuya string
        ir_raw = midea_5bytes_to_raw(ir_data)
        ir_code = encode_tuya_ir(ir_raw)

        # Step 3: Find ZHA device
        zha_device = get_zha_device(hass, ieee)
        if zha_device is None:
            _LOGGER.error("ZHA device not found: %s", ieee)
            return

        _LOGGER.debug("Found ZHA device: %s", zha_device.ieee)

        _LOGGER.debug(
            "Sending IR Follow Me command to %s: temp=%s%s",
            zha_device.ieee,
            temperature,
            temp_symbol,
        )

        if not await async_send_ir_code(zha_device, ir_code):
            return

        _LOGGER.debug(
            "IR Follow Me command sent successfully to %s", zha_device.ieee
        )
    except ValueError as err:
        _LOGGER.error("Invalid temperature value: %s", err)
    except Exception as err:  # noqa: BLE001
        _LOGGER.error("Error sending Follow Me command: %s", err, exc_info=True)


async def async_handle_debug_follow_me(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle the debug_follow_me service call."""
    temperature = call.data[CONF_TEMPERATURE]
    use_fahrenheit = call.data.get(CONF_USE_FAHRENHEIT, False)
    beeper = call.data.get(CONF_BEEPER, False)

    try:
        # Build the Midea Follow Me command bytes
        ir_data = build_midea_follow_me_data(temperature, use_fahrenheit, beeper)

        # Convert those bytes to raw timings
        raw_ir = midea_5bytes_to_raw(ir_data)

        # Encode the raw timings to the Tuya string format
        tuya_code = encode_tuya_ir(raw_ir)

        # Optional: for comparison
        tuya_code_base64 = base64.b64encode(bytes(ir_data)).decode()

        result = {
            "input": {
                "temperature": temperature,
                "use_fahrenheit": use_fahrenheit,
                "beeper": beeper,
            },
            "midea_packet": {
                "hex": " ".join(f"{b:02X}" for b in ir_data),
                "bytes": ir_data,
                "checksum": f"0x{ir_data[5]:02X}",
            },
            "raw_ir_timing_us": raw_ir,
            "tuya_ir_code": tuya_code,
            "tuya_ir_code_len": len(tuya_code),
            "zha_issue_zigbee_cluster_command": {
                "ieee": "<device_ieee_here>",
                "endpoint_id": 1,
                "cluster_id": 0xE004,
                "cluster_type": "in",
                "command": 2,
                "command_type": "server",
                "params": {
                    "code": tuya_code
                },
            },
            "byte_details": {
                "byte_0_type": f"0x{ir_data[0]:02X} (Follow Me Type)",
                "byte_1_const": f"0x{ir_data[1]:02X}",
                "byte_2_flags": (
                    f"0x{ir_data[2]:02X} (Fahrenheit: {bool(ir_data[2] & 0x20)})"
                ),
                "byte_3_flags": (
                    f"0x{ir_data[3]:02X} (Beeper: {bool(ir_data[3] & 0x80)})"
                ),
                "byte_4_temp": f"0x{ir_data[4]:02X} (Encoded temp)",
                "byte_5_checksum": f"0x{ir_data[5]:02X}",
            },
            "comparison": {
                "midea_base64_legacy": tuya_code_base64,
                "tuya_ir_string": tuya_code,
            },
        }

        _LOGGER.info("Midea Follow Me Debug:\n%s", json.dumps(result, indent=2))
    except Exception as err:  # noqa: BLE001
        _LOGGER.error("Debug error: %s", err, exc_info=True)


async def async_register_services(hass: HomeAssistant) -> None:
    """Register the integration services."""
    if not hass.services.has_service(DOMAIN, SERVICE_FOLLOW_ME):
        hass.services.async_register(
            DOMAIN,
            SERVICE_FOLLOW_ME,
            partial(async_handle_send_follow_me, hass),
            schema=SERVICE_FOLLOW_ME_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_DEBUG):
        hass.services.async_register(
            DOMAIN,
            SERVICE_DEBUG,
            partial(async_handle_debug_follow_me, hass),
            schema=SERVICE_DEBUG_SCHEMA,
        )


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove the integration services."""
    for service in (SERVICE_FOLLOW_ME, SERVICE_DEBUG):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
