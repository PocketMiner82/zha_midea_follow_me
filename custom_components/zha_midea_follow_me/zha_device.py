"""Helpers to discover ZHA devices and send IR commands to them."""
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    CLUSTER_COMMAND_SERVER,
    CLUSTER_TYPE_IN,
    CLUSTER_ZOSUNG_IR_CONTROL,
    COMMAND_IR_SEND,
    ZHA_DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def zha_gateway_available(hass: HomeAssistant) -> bool:
    """Return True if the ZHA gateway is available."""
    zha_data = hass.data.get(ZHA_DOMAIN)
    if zha_data is None:
        return False
    return getattr(zha_data, "gateway_proxy", None) is not None


def get_zha_devices(hass: HomeAssistant) -> dict[str, Any]:
    """Return a mapping of IEEE address to ZHA device."""
    zha_data = hass.data.get(ZHA_DOMAIN)
    if zha_data is None:
        _LOGGER.debug("ZHA integration not available")
        return {}

    gateway_proxy = getattr(zha_data, "gateway_proxy", None)
    if gateway_proxy is None:
        _LOGGER.debug("ZHA gateway is not ready yet")
        return {}

    devices = getattr(gateway_proxy.gateway, "devices", None) or {}
    return {str(ieee): zha_device for ieee, zha_device in devices.items()}


def device_supports_ir_control(zha_device: Any) -> bool:
    """Return True if the ZHA device exposes the Zosung IR Control cluster."""
    for cluster_map in zha_device.async_get_clusters().values():
        if CLUSTER_ZOSUNG_IR_CONTROL in cluster_map.get(CLUSTER_TYPE_IN, {}):
            return True
    return False


def is_ir_blaster(hass: HomeAssistant, ieee: str) -> bool:
    """Return True if the device with the given IEEE is an IR blaster."""
    zha_device = get_zha_devices(hass).get(ieee)
    if zha_device is None:
        return False
    return device_supports_ir_control(zha_device)


def get_ir_blasters(hass: HomeAssistant) -> dict[str, str]:
    """Return a mapping of IEEE address to friendly name for IR blasters."""
    device_registry = hass.data.get(dr.DATA_REGISTRY)

    blasters: dict[str, str] = {}
    for ieee, zha_device in get_zha_devices(hass).items():
        if not device_supports_ir_control(zha_device):
            continue
        name = ieee
        if device_registry is not None:
            device_entry = device_registry.async_get_device(
                identifiers={(ZHA_DOMAIN, ieee)}
            )
            if device_entry is not None:
                name = device_entry.name_by_user or device_entry.name or ieee
        blasters[ieee] = name
    return blasters


def get_zha_device(hass: HomeAssistant, ieee: str) -> Any | None:
    """Return the ZHA device for the given IEEE address."""
    zha_devices = get_zha_devices(hass)
    if not zha_devices:
        _LOGGER.error("No ZHA devices found")
        return None

    if ieee in zha_devices:
        return zha_devices[ieee]

    _LOGGER.error("Device not found: %s", ieee)
    _LOGGER.error(
        "Available ZHA devices: %s", list(zha_devices)
    )
    return None


async def async_send_ir_code(zha_device: Any, ir_code: str) -> bool:
    """Send an IR code to a ZHA device using the Zosung IR Control cluster."""
    endpoint_id = None
    for ep_id, cluster_map in zha_device.async_get_clusters().items():
        if CLUSTER_ZOSUNG_IR_CONTROL in cluster_map.get(CLUSTER_TYPE_IN, {}):
            endpoint_id = ep_id
            _LOGGER.debug("Found endpoint %s with cluster 0xE004", ep_id)
            break

    if endpoint_id is None:
        _LOGGER.error(
            "No Zosung IR Control cluster (0xE004) found on device %s",
            zha_device.ieee,
        )
        return False

    try:
        # The Zosung IR Control quirk wraps the code in the Tuya IR envelope
        # itself, so only the base64 IR string is passed here.
        await zha_device.issue_cluster_command(
            endpoint_id,
            CLUSTER_ZOSUNG_IR_CONTROL,
            COMMAND_IR_SEND,
            CLUSTER_COMMAND_SERVER,
            None,
            {"code": ir_code},
            cluster_type=CLUSTER_TYPE_IN,
        )
    except Exception as err:
        _LOGGER.error(
            "Failed to send IR command to cluster 0xE004: %s", err, exc_info=True
        )
        return False

    return True
