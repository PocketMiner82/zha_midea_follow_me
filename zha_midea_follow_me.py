import base64
import io
import json
import logging
import math
from base64 import encodebytes
from bisect import bisect
from struct import pack
from typing import Any, Optional

_LOGGER = logging.getLogger("pyscript.zha_midea_follow_me")

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


def encode_tuya_ir(signal: list[int], compression_level=2) -> str:
    """
    Encodes an IR signal (see `decode_tuya_ir`)
    into an IR code string for a Tuya blaster.
    """
    payload = b"".join(pack("<H", t) for t in signal)
    compress(out := io.BytesIO(), payload, compression_level)
    payload = out.getvalue()
    return encodebytes(payload).decode("ascii").replace("\n", "")


def emit_literal_blocks(out: io.FileIO, data: bytes):
    for i in range(0, len(data), 32):
        emit_literal_block(out, data[i : i + 32])


def emit_literal_block(out: io.FileIO, data: bytes):
    length = len(data) - 1
    assert 0 <= length < (1 << 5)
    out.write(bytes([length]))
    out.write(data)


def emit_distance_block(out: io.FileIO, length: int, distance: int):
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


def compress(out: io.FileIO, data: bytes, level=2):
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
    
    raw = []
    
    # First transmission (normal polarity)
    raw.append(HEADER_MARK_US)
    raw.append(HEADER_SPACE_US)
    
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
    """Encode temperature to Midea format (byte 4 value).

    Args:
        temperature: Temperature value (int)
        use_fahrenheit: If True, interpret as Fahrenheit; otherwise Celsius

    Returns:
        Encoded temperature byte value
    """
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
    """Build Midea Follow Me IR data payload.

    This builds the 6-byte Midea protocol packet as defined in:
    https://github.com/esphome/esphome/blob/dev/esphome/components/midea/ir_transmitter.h
    
    Packet structure:
    - Byte 0: Type (0xA4 = Follow Me)
    - Byte 1: Constant (0x82)
    - Byte 2: Control flags (bit 5 = Fahrenheit flag)
    - Byte 3: Flags (bit 7 = Beeper)
    - Byte 4: Temperature (encoded)
    - Byte 5: Checksum (XOR of bytes 0-4)

    Args:
        temperature: Temperature value (float)
        use_fahrenheit: If True, interpret as Fahrenheit; otherwise Celsius
        beeper: Enable beeper feedback

    Returns:
        List of 6 bytes representing the IR command

    Raises:
        ValueError: If temperature is not finite
    """
    # Validate temperature is finite
    if not math.isfinite(temperature):
        raise ValueError(f"Temperature must be finite, got: {temperature}")

    # Round temperature to nearest integer (mimics std::lroundf)
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


def _build_ir_send_payload(ir_code: str) -> str:
    """Build the JSON payload for ZHA IRSend command.
    
    The Zosung IR Control cluster expects a JSON-formatted string with:
    - key_num: Number of keys (1 for single IR command)
    - delay: Delay in ms between keys
    - key1: Key definition with:
        - num: Key index
        - freq: Carrier frequency (38000 Hz for most IR devices)
        - type: Command type (1 = raw IR)
        - key_code: Base64-encoded IR data

    Args:
        ir_code: Base64-encoded IR packet
        
    Returns:
        JSON string for ZHA command
    """
    payload = {
        "key_num": 1,
        "delay": 300,
        "key1": {
            "num": 1,
            "freq": 38000,
            "type": 1,
            "key_code": ir_code
        }
    }
    return json.dumps(payload)


async def _find_zha_device(device_id: str) -> Optional[Any]:
    """Find ZHA device from entity ID or IEEE address.

    Args:
        device_id: Entity ID (e.g., "switch.moes_ufo_r11") or IEEE address

    Returns:
        ZHA device object or None if not found
    """
    try:
        zha_data = hass.data.get("zha")
        if zha_data is None:
            _LOGGER.error("ZHA integration not available")
            return None

        zha_device_registry = zha_data.get("devices", {})
        if not zha_device_registry:
            _LOGGER.error("No ZHA devices found")
            return None

        # Try direct IEEE address lookup
        if device_id in zha_device_registry:
            return zha_device_registry[device_id]

        # Try lookup by entity ID via device registry
        device_registry = hass.data.get("device_registry")
        entity_registry = hass.data.get("entity_registry")

        if device_registry and entity_registry:
            # Try to find entity entry
            for entity_id_key, entity_entry in entity_registry.items():
                if device_id.lower() in entity_id_key.lower():
                    if entity_entry.device_id:
                        device_entry = device_registry.devices.get(entity_entry.device_id)
                        if device_entry:
                            # Search ZHA devices by IEEE
                            for ieee_addr in device_entry.identifiers:
                                if isinstance(ieee_addr, tuple) and len(ieee_addr) == 2:
                                    if ieee_addr[0] == "zigpy":
                                        zha_device = zha_device_registry.get(ieee_addr[1])
                                        if zha_device:
                                            return zha_device

        _LOGGER.error(f"Device not found: {device_id}")
        _LOGGER.error(f"Available ZHA devices: {list(zha_device_registry.keys())}")
        return None

    except Exception as e:
        _LOGGER.error(f"Error finding ZHA device: {e}", exc_info=True)
        return None


@service
async def zha_midea_follow_me(
    device_id: str,
    temperature: float,
    use_fahrenheit: bool = False,
    beeper: bool = False
) -> None:
    """yaml
name: Midea Follow Me IR
description: >
    Sends a Midea Follow Me IR command through a Moes UFO-R11 (TS1201) 
    or similar ZHA-connected IR blaster device, mimicking the ESPHome 
    do_follow_me() function behavior.
fields:
    device_id:
        description: ZHA device entity ID
        example: "switch.moes_ufo_r11_switch"
        required: true
        selector:
            entity:
                integration: zha
                domain: switch
    temperature:
        description: Temperature value to send
        example: 22.5
        required: true
        selector:
            number:
                mode: box
                step: 0.5
    use_fahrenheit:
        description: Use Fahrenheit scale instead of Celsius
        example: false
        required: false
        selector:
            boolean: {}
    beeper:
        description: Enable beeper feedback on the device
        example: true
        required: false
        selector:
            boolean: {}
"""

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

        # Step 3: Build JSON payload for ZHA command
        json_payload = _build_ir_send_payload(ir_code)
        _LOGGER.debug(f"ZHA Payload: {json_payload}")

        # Step 4: Find ZHA device
        zha_device = await _find_zha_device(device_id)
        if zha_device is None:
            _LOGGER.error(f"ZHA device not found: {device_id}")
            return

        _LOGGER.debug(f"Found ZHA device: {zha_device.ieee}")

        # Step 5: Find endpoint with Zosung IR Control cluster
        endpoint = None
        for ep_id, ep in zha_device.endpoints.items():
            if ep_id == 0:  # Skip endpoint 0
                continue
            if CLUSTER_ZOSUNG_IR_CONTROL in ep.clusters:
                endpoint = ep
                _LOGGER.debug(f"Found endpoint {ep_id} with cluster 0xE004")
                break

        if endpoint is None:
            _LOGGER.error(
                f"No Zosung IR Control cluster (0xE004) found on device {zha_device.ieee}"
            )
            return

        # Step 6: Get the cluster and send command
        ir_control_cluster = endpoint.clusters[CLUSTER_ZOSUNG_IR_CONTROL]

        _LOGGER.debug(
            f"Sending IR Follow Me command to {zha_device.ieee}: "
            f"temp={temperature}{temp_symbol}"
        )

        try:
            # Send IRSend command (0x02) with code parameter
            result = await ir_control_cluster.command(
                COMMAND_IR_SEND,
                code=ir_code
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


@service
async def zha_midea_follow_me_debug(
    temperature: float,
    use_fahrenheit: bool = False,
    beeper: bool = False
) -> None:
    """yaml
name: Midea Follow Me IR Debug
description: >
    Debug service to display the final Tuya IR payload used by TS1201, 
    logging the Midea 6-byte Follow Me packet, raw IR timings, final 
    Tuya-encoded IR string, and the exact ZHA command payload.
fields:
    temperature:
        description: Temperature value to evaluate
        example: 22.5
        required: true
        selector:
            number:
                mode: box
                step: 0.1
    use_fahrenheit:
        description: Use Fahrenheit scale instead of Celsius
        example: false
        selector:
            boolean: {}
    beeper:
        description: Enable beeper feedback on the device
        example: true
        selector:
            boolean: {}
"""
    try:
        # 1) Build the Midea Follow Me command bytes
        ir_data = _build_midea_follow_me_data(temperature, use_fahrenheit, beeper)

        # 2) Convert those bytes to raw timings
        raw_ir = midea_5bytes_to_raw(ir_data)

        # 3) Encode the raw timings to the Tuya string format
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