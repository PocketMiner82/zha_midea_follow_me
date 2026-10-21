"""The ZHA Midea Follow Me component."""
import base64
import io
import json
import logging
import math
from base64 import encodebytes
from bisect import bisect
from struct import pack
from typing import Any, Optional

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

DOMAIN = "zha_midea_follow_me"

# Midea Protocol Constants
MIDEA_TYPE_FOLLOW_ME = 0xA4
MIDEA_CONST_BYTE1 = 0x82
MIDEA_CONST_BYTE2 = 0x48
MIDEA_CONST_BYTE3 = 0x7F

# Temperature constraints (Celsius)
MIN_TEMP_C = 0
MAX_TEMP_C = 37
# Temperature constraints (Fahrenheit)
MIN_TEMP_F = 32
MAX_TEMP_F = 99

# ZHA cluster IDs
CLUSTER_ZOSUNG_IR_CONTROL = 0xE004
COMMAND_IR_SEND = 0x02

# ZHA integration constants
ZHA_DOMAIN = "zha"
CLUSTER_TYPE_IN = "in"
CLUSTER_COMMAND_SERVER = "server"

SERVICE_FOLLOW_ME = "send_follow_me"
SERVICE_DEBUG = "debug_follow_me"

SERVICE_FOLLOW_ME_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("temperature"): vol.Coerce(float),
        vol.Optional("use_fahrenheit", default=False): cv.boolean,
        vol.Optional("beeper", default=False): cv.boolean,
    }
)

SERVICE_DEBUG_SCHEMA = vol.Schema(
    {
        vol.Required("temperature"): vol.Coerce(float),
        vol.Optional("use_fahrenheit", default=False): cv.boolean,
        vol.Optional("beeper", default=False): cv.boolean,
    }
)


def encode_tuya_ir(signal: list[int], compression_level=2) -> str:
    """
    Encodes an IR signal (see `decode_tuya_ir`)
    into an IR code string for a Tuya blaster.
    """
    payload = b"".join(pack("<H", t) for t in signal)
    compress(out := io.BytesIO(), payload, compression_level)
    payload = out.getvalue()
    return encodebytes(payload).decode("ascii").replace("\n", "")


def emit_literal_blocks(out: io.BytesIO, data: bytes):
    for i in range(0, len(data), 32):
        emit_literal_block(out, data[i : i + 32])


def emit_literal_block(out: io.BytesIO, data: bytes):
    length = len(data) - 1
    assert 0 <= length < (1 << 5)
    out.write(bytes([length]))
    out.write(data)


def emit_distance_block(out: io.BytesIO, length: int, distance: int):
    distance -= 1
    assert 0 <= distance < (1 << 13)
    length -= 2
    assert length > 0
    block = bytearray()
    if length >= 7:
        assert length - 7 < (1 << 8)
        block.append(length - 7)
        length = 7
    block.insert(0, length << 5 | distance >> 8)
    block.append(distance & 0xFF)
    out.write(block)


def compress(out: io.BytesIO, data: bytes, level=2):
    """
    Takes a byte string and outputs a compressed "Tuya stream".
    Implemented compression levels:
    0 - copy over (no compression, 3.1% overhead)
    1 - eagerly use first length-distance pair found (linear)
    2 - eagerly use best length-distance pair found
    3 - optimal compression (n^3)
    """
    if level == 0:
        return emit_literal_blocks(out, data)

    W = 2**13  # window size
    L = 255 + 9  # maximum length

    def distance_candidates():
        return range(1, min(pos, W) + 1)

    def find_length_for_distance(start: int) -> int:
        length = 0
        limit = min(L, len(data) - pos)
        while length < limit and data[pos + length] == data[start + length]:
            length += 1
        return length

    def find_length_candidates():
        return ((find_length_for_distance(pos - d), d) for d in distance_candidates())

    def find_length_cheap():
        return next((c for c in find_length_candidates() if c[0] >= 3), None)

    def find_length_max():
        return max(find_length_candidates(), key=lambda c: (c[0], -c[1]), default=None)

    if level >= 2:
        suffixes = []
        next_pos = 0

        def key(n):
            return data[n:]

        def find_idx(n):
            return bisect(suffixes, key(n), key=key)

        def distance_candidates():
            nonlocal next_pos
            while next_pos <= pos:
                if len(suffixes) == W:
                    suffixes.pop(find_idx(next_pos - W))
                suffixes.insert(idx := find_idx(next_pos), next_pos)
                next_pos += 1
            idxs = (idx + i for i in (+1, -1))  # try +1 first
            return (pos - suffixes[i] for i in idxs if 0 <= i < len(suffixes))

    if level <= 2:
        find_length = {1: find_length_cheap, 2: find_length_max}[level]
        block_start = pos = 0
        while pos < len(data):
            if (c := find_length()) and c[0] >= 3:
                emit_literal_blocks(out, data[block_start:pos])
                emit_distance_block(out, c[0], c[1])
                pos += c[0]
                block_start = pos
            else:
                pos += 1
        emit_literal_blocks(out, data[block_start:pos])
        return

    # use topological sort to find shortest path
    predecessors = [(0, None, None)] + [None] * len(data)

    def put_edge(cost, length, distance):
        npos = pos + length
        cost += predecessors[pos][0]
        current = predecessors[npos]
        if not current or cost < current[0]:
            predecessors[npos] = cost, length, distance

    for pos in range(len(data)):
        if c := find_length_max():
            for length in range(3, c[0] + 1):
                put_edge(2 if length < 9 else 3, length, c[1])
        for bit_length in range(1, min(32, len(data) - pos) + 1):
            put_edge(1 + bit_length, bit_length, 0)

    # reconstruct path, emit blocks
    blocks = []
    pos = len(data)
    while pos > 0:
        _, length, distance = predecessors[pos]
        pos -= length
        blocks.append((pos, length, distance))
    for pos, length, distance in reversed(blocks):
        if not distance:
            emit_literal_block(out, data[pos : pos + length])
        else:
            emit_distance_block(out, length, distance)


def reverse_bits(byte):
    """Reverse the bits in a byte (MSB <-> LSB)."""
    result = 0
    for i in range(8):
        result = (result << 1) | (byte & 1)
        byte >>= 1
    return result


def midea_5bytes_to_raw(code_bytes):
    """
    Convert 5 Midea code bytes to raw IR timings.
    Matches ESPHome's MideaProtocol::encode()
    """
    TICK_US = 560
    HEADER_MARK_US = 8 * TICK_US      # 4480
    HEADER_SPACE_US = 8 * TICK_US     # 4480
    BIT_MARK_US = 1 * TICK_US         # 560
    BIT_ONE_SPACE_US = 3 * TICK_US    # 1680
    BIT_ZERO_SPACE_US = 1 * TICK_US   # 560
    FOOTER_MARK_US = 1 * TICK_US      # 560
    FOOTER_SPACE_US = 10 * TICK_US    # 5600

    # Build 6-byte payload with checksum
    data = list(code_bytes) + [0]
    cs = 0
    for i in range(5):
        cs -= reverse_bits(data[i])
    data[5] = reverse_bits(cs) & 0xFF

    # First transmission (normal polarity)
    raw = [HEADER_MARK_US, HEADER_SPACE_US]

    for byte_idx in range(6):
        byte = data[byte_idx]
        for bit_idx in range(8):
            raw.append(BIT_MARK_US)
            # Check MSB first
            if byte & 0x80:
                raw.append(BIT_ONE_SPACE_US)
            else:
                raw.append(BIT_ZERO_SPACE_US)
            byte = byte << 1
    
    raw.append(FOOTER_MARK_US)
    raw.append(FOOTER_SPACE_US)
    
    # Second transmission (inverted polarity)
    raw.append(HEADER_MARK_US)
    raw.append(HEADER_SPACE_US)
    
    for byte_idx in range(6):
        byte = data[byte_idx]
        for bit_idx in range(8):
            raw.append(BIT_MARK_US)
            # Inverted
            if byte & 0x80:
                raw.append(BIT_ZERO_SPACE_US)
            else:
                raw.append(BIT_ONE_SPACE_US)
            byte = byte << 1
    
    raw.append(FOOTER_MARK_US)
    
    return raw


def _clamp(value: int, min_val: int, max_val: int) -> int:
    """Clamp value between min and max."""
    return max(min_val, min(max_val, value))


def _calculate_checksum(data: list) -> int:
    """Calculate Midea checksum (XOR of first 5 bytes)."""
    checksum = 0
    for byte in data[:5]:
        checksum ^= byte
    return checksum


def _encode_temperature(temperature: int, use_fahrenheit: bool) -> int:
    """Encode temperature to Midea format (byte 4 value)."""
    if use_fahrenheit:
        # Clamp to Fahrenheit range and encode as (value - 31)
        temp_clamped = _clamp(temperature, MIN_TEMP_F, MAX_TEMP_F)
        return temp_clamped - 31
    else:
        # Clamp to Celsius range and encode as (value + 1)
        temp_clamped = _clamp(temperature, MIN_TEMP_C, MAX_TEMP_C)
        return temp_clamped + 1


def _build_midea_follow_me_data(
    temperature: float,
    use_fahrenheit: bool = False,
    beeper: bool = False
) -> list:
    """Build Midea Follow Me IR data payload."""
    # Validate temperature is finite
    if not math.isfinite(temperature):
        raise ValueError(f"Temperature must be finite, got: {temperature}")

    # Round temperature to nearest integer
    temp_int = round(temperature)

    # Build data bytes
    data = [
        MIDEA_TYPE_FOLLOW_ME,  # Byte 0: Type (0xA4)
        MIDEA_CONST_BYTE1,     # Byte 1: 0x82
        MIDEA_CONST_BYTE2,     # Byte 2: 0x48 (base control flags)
        MIDEA_CONST_BYTE3,     # Byte 3: 0x7F (base flags)
        0x00,                  # Byte 4: Temperature (placeholder)
        0x00                   # Byte 5: Checksum (placeholder)
    ]

    # Encode temperature into byte 4
    data[4] = _encode_temperature(temp_int, use_fahrenheit)

    # Set Fahrenheit flag in byte 2 (bit 5 = 0x20)
    if use_fahrenheit:
        data[2] |= 0x20

    # Set Beeper flag in byte 3 (bit 7 = 0x80)
    if beeper:
        data[3] |= 0x80

    # Calculate and set checksum (XOR of first 5 bytes)
    data[5] = _calculate_checksum(data)

    return data


async def _find_zha_device(hass: HomeAssistant, device_id: str) -> Optional[Any]:
    """Find a ZHA device from entity ID or IEEE address."""
    try:
        zha_data = hass.data.get(ZHA_DOMAIN)
        if zha_data is None:
            _LOGGER.error("ZHA integration not available")
            return None

        gateway_proxy = getattr(zha_data, "gateway_proxy", None)
        if gateway_proxy is None:
            _LOGGER.error("ZHA gateway is not ready yet")
            return None

        zha_devices = gateway_proxy.gateway.devices
        if not zha_devices:
            _LOGGER.error("No ZHA devices found")
            return None

        # Try direct IEEE address lookup
        for ieee, zha_device in zha_devices.items():
            if str(ieee) == device_id:
                return zha_device

        # Try lookup by entity ID via device registry
        entity_registry = hass.data.get(er.DATA_REGISTRY)
        if entity_registry is None:
            _LOGGER.error("Entity registry is not available")
            return None

        entity_entry = entity_registry.async_get(device_id)
        if entity_entry is not None and entity_entry.device_id:
            device_registry = hass.data.get(dr.DATA_REGISTRY)
            if device_registry is None:
                _LOGGER.error("Device registry is not available")
                return None

            device_entry = device_registry.async_get(entity_entry.device_id)
            if device_entry is not None:
                # ZHA devices are registered under the "zha" domain
                for domain, identifier in device_entry.identifiers:
                    if domain != ZHA_DOMAIN:
                        continue
                    for ieee, zha_device in zha_devices.items():
                        if str(ieee) == identifier:
                            return zha_device

        _LOGGER.error(f"Device not found: {device_id}")
        _LOGGER.error(f"Available ZHA devices: {[str(ieee) for ieee in zha_devices]}")
        return None

    except Exception as e:
        _LOGGER.error(f"Error finding ZHA device: {e}", exc_info=True)
        return None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the ZHA Midea Follow Me component."""
    
    async def handle_send_follow_me(call: ServiceCall) -> None:
        """Handle the send_follow_me service call."""
        device_id = call.data["device_id"]
        temperature = call.data["temperature"]
        use_fahrenheit = call.data.get("use_fahrenheit", False)
        beeper = call.data.get("beeper", False)
        
        temp_symbol = '°F' if use_fahrenheit else '°C'
        _LOGGER.debug(
            f"Sending Midea Follow Me command: temp={temperature}{temp_symbol}, "
            f"beeper={beeper} to {device_id}"
        )
        
        try:
            # Step 1: Build IR data payload
            ir_data = _build_midea_follow_me_data(temperature, use_fahrenheit, beeper)
            ir_hex = " ".join(f"{b:02X}" for b in ir_data)
            _LOGGER.debug(f"IR Data (hex): {ir_hex}")
            
            # Step 2: Convert 5 bytes to raw and then to tuya string
            ir_raw = midea_5bytes_to_raw(ir_data)
            ir_code = encode_tuya_ir(ir_raw)
            
            # Step 3: Find ZHA device
            zha_device = await _find_zha_device(hass, device_id)
            if zha_device is None:
                _LOGGER.error(f"ZHA device not found: {device_id}")
                return
            
            _LOGGER.debug(f"Found ZHA device: {zha_device.ieee}")
            
            # Step 4: Find endpoint with Zosung IR Control cluster
            endpoint_id = None
            for ep_id, cluster_map in zha_device.async_get_clusters().items():
                if CLUSTER_ZOSUNG_IR_CONTROL in cluster_map.get(CLUSTER_TYPE_IN, {}):
                    endpoint_id = ep_id
                    _LOGGER.debug(f"Found endpoint {ep_id} with cluster 0xE004")
                    break

            if endpoint_id is None:
                _LOGGER.error(
                    f"No Zosung IR Control cluster (0xE004) found on device {zha_device.ieee}"
                )
                return

            _LOGGER.debug(
                f"Sending IR Follow Me command to {zha_device.ieee}: "
                f"temp={temperature}{temp_symbol}"
            )

            try:
                # Send IRSend command (0x02) with the raw Tuya encoded IR code.
                # The Zosung IR Control quirk wraps the code in the Tuya IR
                # envelope itself, so only the base64 IR string is passed here.
                await zha_device.issue_cluster_command(
                    endpoint_id,
                    CLUSTER_ZOSUNG_IR_CONTROL,
                    COMMAND_IR_SEND,
                    CLUSTER_COMMAND_SERVER,
                    None,
                    {"code": ir_code},
                    cluster_type=CLUSTER_TYPE_IN,
                )
                _LOGGER.debug(
                    f"IR Follow Me command sent successfully to {zha_device.ieee}"
                )
            except Exception as e:
                _LOGGER.error(
                    f"Failed to send IR command to cluster 0xE004: {e}",
                    exc_info=True
                )
                return
                
        except ValueError as e:
            _LOGGER.error(f"Invalid temperature value: {e}")
        except Exception as e:
            _LOGGER.error(f"Error sending Follow Me command: {e}", exc_info=True)
    
    async def handle_debug_follow_me(call: ServiceCall) -> None:
        """Handle the debug_follow_me service call."""
        temperature = call.data["temperature"]
        use_fahrenheit = call.data.get("use_fahrenheit", False)
        beeper = call.data.get("beeper", False)
        
        try:
            # Build the Midea Follow Me command bytes
            ir_data = _build_midea_follow_me_data(temperature, use_fahrenheit, beeper)
            
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
                    "byte_2_flags": f"0x{ir_data[2]:02X} (Fahrenheit: {bool(ir_data[2] & 0x20)})",
                    "byte_3_flags": f"0x{ir_data[3]:02X} (Beeper: {bool(ir_data[3] & 0x80)})",
                    "byte_4_temp": f"0x{ir_data[4]:02X} (Encoded temp)",
                    "byte_5_checksum": f"0x{ir_data[5]:02X}",
                },
                "comparison": {
                    "midea_base64_legacy": tuya_code_base64,
                    "tuya_ir_string": tuya_code,
                },
            }
            
            _LOGGER.info(f"Midea Follow Me Debug:\n{json.dumps(result, indent=2)}")
            
        except Exception as e:
            _LOGGER.error(f"Debug error: {e}", exc_info=True)
    
    # Register services
    hass.services.async_register(
        DOMAIN, SERVICE_FOLLOW_ME, handle_send_follow_me, schema=SERVICE_FOLLOW_ME_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DEBUG, handle_debug_follow_me, schema=SERVICE_DEBUG_SCHEMA
    )
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry) -> bool:
    """Unload a config entry."""
    return True
