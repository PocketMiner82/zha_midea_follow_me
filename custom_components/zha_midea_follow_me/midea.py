"""Midea Follow Me IR protocol encoding."""
import math

from .const import (
    MAX_TEMP_C,
    MAX_TEMP_F,
    MIDEA_CONST_BYTE1,
    MIDEA_CONST_BYTE2,
    MIDEA_CONST_BYTE3,
    MIDEA_FLAG_BEEPER,
    MIDEA_FLAG_FAHRENHEIT,
    MIDEA_TYPE_FOLLOW_ME,
    MIN_TEMP_C,
    MIN_TEMP_F,
)


def reverse_bits(byte: int) -> int:
    """Reverse the bits in a byte (MSB <-> LSB)."""
    result = 0
    for _ in range(8):
        result = (result << 1) | (byte & 1)
        byte >>= 1
    return result


def midea_5bytes_to_raw(code_bytes: list[int]) -> list[int]:
    """
    Convert 5 Midea code bytes to raw IR timings.
    Matches ESPHome's MideaProtocol::encode()
    """
    tick_us = 560
    header_mark_us = 8 * tick_us  # 4480
    header_space_us = 8 * tick_us  # 4480
    bit_mark_us = 1 * tick_us  # 560
    bit_one_space_us = 3 * tick_us  # 1680
    bit_zero_space_us = 1 * tick_us  # 560
    footer_mark_us = 1 * tick_us  # 560
    footer_space_us = 10 * tick_us  # 5600

    # Build 6-byte payload with checksum
    data = list(code_bytes) + [0]
    cs = 0
    for i in range(5):
        cs -= reverse_bits(data[i])
    data[5] = reverse_bits(cs) & 0xFF

    # First transmission (normal polarity)
    raw = [header_mark_us, header_space_us]

    for byte_idx in range(6):
        byte = data[byte_idx]
        for _ in range(8):
            raw.append(bit_mark_us)
            # Check MSB first
            if byte & 0x80:
                raw.append(bit_one_space_us)
            else:
                raw.append(bit_zero_space_us)
            byte = byte << 1

    raw.append(footer_mark_us)
    raw.append(footer_space_us)

    # Second transmission (inverted polarity)
    raw.append(header_mark_us)
    raw.append(header_space_us)

    for byte_idx in range(6):
        byte = data[byte_idx]
        for _ in range(8):
            raw.append(bit_mark_us)
            # Inverted
            if byte & 0x80:
                raw.append(bit_zero_space_us)
            else:
                raw.append(bit_one_space_us)
            byte = byte << 1

    raw.append(footer_mark_us)

    return raw


def clamp(value: int, min_val: int, max_val: int) -> int:
    """Clamp value between min and max."""
    return max(min_val, min(max_val, value))


def calculate_checksum(data: list[int]) -> int:
    """Calculate Midea checksum (XOR of first 5 bytes)."""
    checksum = 0
    for byte in data[:5]:
        checksum ^= byte
    return checksum


def encode_temperature(temperature: int, use_fahrenheit: bool) -> int:
    """Encode temperature to Midea format (byte 4 value)."""
    if use_fahrenheit:
        # Clamp to Fahrenheit range and encode as (value - 31)
        temp_clamped = clamp(temperature, MIN_TEMP_F, MAX_TEMP_F)
        return temp_clamped - 31
    # Clamp to Celsius range and encode as (value + 1)
    temp_clamped = clamp(temperature, MIN_TEMP_C, MAX_TEMP_C)
    return temp_clamped + 1


def build_midea_follow_me_data(
    temperature: float,
    use_fahrenheit: bool = False,
    beeper: bool = False,
) -> list[int]:
    """Build Midea Follow Me IR data payload."""
    # Validate temperature is finite
    if not math.isfinite(temperature):
        raise ValueError(f"Temperature must be finite, got: {temperature}")

    # Round temperature to nearest integer
    temp_int = round(temperature)

    # Build data bytes
    data = [
        MIDEA_TYPE_FOLLOW_ME,  # Byte 0: Type (0xA4)
        MIDEA_CONST_BYTE1,  # Byte 1: 0x82
        MIDEA_CONST_BYTE2,  # Byte 2: 0x48 (base control flags)
        MIDEA_CONST_BYTE3,  # Byte 3: 0x7F (base flags)
        0x00,  # Byte 4: Temperature (placeholder)
        0x00,  # Byte 5: Checksum (placeholder)
    ]

    # Encode temperature into byte 4
    data[4] = encode_temperature(temp_int, use_fahrenheit)

    # Set Fahrenheit flag in byte 2 (bit 5 = 0x20)
    if use_fahrenheit:
        data[2] |= MIDEA_FLAG_FAHRENHEIT

    # Set Beeper flag in byte 3 (bit 7 = 0x80)
    if beeper:
        data[3] |= MIDEA_FLAG_BEEPER

    # Calculate and set checksum (XOR of first 5 bytes)
    data[5] = calculate_checksum(data)

    return data
