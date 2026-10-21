"""Constants for the ZHA Midea Follow Me integration."""

DOMAIN = "zha_midea_follow_me"

# Service fields
CONF_BEEPER = "beeper"
CONF_IEEE = "ieee"
CONF_TEMPERATURE = "temperature"
CONF_USE_FAHRENHEIT = "use_fahrenheit"

# Midea protocol constants
MIDEA_TYPE_FOLLOW_ME = 0xA4
MIDEA_CONST_BYTE1 = 0x82
MIDEA_CONST_BYTE2 = 0x48
MIDEA_CONST_BYTE3 = 0x7F
MIDEA_FLAG_FAHRENHEIT = 0x20
MIDEA_FLAG_BEEPER = 0x80

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

# Services
SERVICE_FOLLOW_ME = "send_follow_me"
SERVICE_DEBUG = "debug_follow_me"

# Keys used inside hass.data[DOMAIN]
DATA_TARGET = "target"
DATA_CONFIG_ENTRIES = "config_entries"
