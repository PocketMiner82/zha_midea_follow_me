# ZHA Midea Follow Me

A Home Assistant custom integration that sends the **Midea "Follow Me"** IR command
to a Midea air conditioner (tested with Midea PortaSplit) through a ZHA-connected IR
blaster (e.g. the Moes UFO-R11 / TS1201), so the AC can be set to the temperature the
Home Assistant sensor reports.

The integration builds the Midea protocol packet, converts it to raw IR timings,
encodes those into the Tuya IR format the TS1201 expects, and sends it over the
ZHA *Zosung IR Control* cluster (`0xE004`).

---

## Requirements

- Home Assistant **2024.11** or newer (developed and tested against 2026.2).
- The [ZHA integration](https://www.home-assistant.io/integrations/zha/) set up
  with your Zigbee adapter.
- A ZHA-paired IR blaster that exposes the **Zosung IR Control** cluster
  (`0xE004`), such as:
  - Moes UFO-R11 / TS1201
  - Zemismart ZS08 / Tuya-based `TS1201` clones
  - Any other Tuya `TS1201` compatible blaster supported by `zha-quirks`

> The blaster must be visible in ZHA as a device with the `0xE004` cluster.
> The integration only offers those devices in the setup dialog.

---

## Installation

### Manual installation (recommended for a custom integration)

1. Copy the `custom_components/zha_midea_follow_me` folder of this repository
   into the `custom_components` folder of your Home Assistant configuration
   directory, so that you end up with:

   ```
   <config>/custom_components/zha_midea_follow_me/
   ├── __init__.py
   ├── config_flow.py
   ├── const.py
   ├── helpers.py
   ├── manifest.json
   ├── midea.py
   ├── services.yaml
   ├── services.py
   ├── strings.json
   ├── targets.py
   ├── translations/
   │   └── en.json
   ├── tuya_ir.py
   └── zha_device.py
   ```

2. Restart Home Assistant.
3. Make sure the ZHA integration is already set up and your IR blaster is
   paired. The ZHA integration is a dependency, so it is started automatically.

### HACS

This integration is not published to HACS. If you want to manage it with HACS,
add this repository as a [HACS custom repository](https://hacs.xyz/docs/faq/custom_repositories/)
and make sure the integration lives in `custom_components/zha_midea_follow_me`.

---

## Setup

1. Go to **Settings → Devices & Services**.
2. Click **Add Integration** in the bottom right and search for
   **ZHA Midea Follow Me**.
3. Select your IR blaster from the dropdown. Only devices with the Zosung IR
   Control cluster (`0xE004`) are listed.
4. Press **Submit**. The integration is now set up and the blaster is linked to
   the config entry.

The selected blaster is remembered, so the services can be called without
specifying a target.

### Changing the blaster later

Open the integration in **Settings → Devices & Services**, click the
**⋮** menu next to the entry and choose **Reconfigure** to pick a different
IR blaster.

### YAML setup (optional)

The integration also works without the UI. Add the following to
`configuration.yaml` to set the default blaster:

```yaml
zha_midea_follow_me:
  entity_id: switch.moes_ufo_r11_switch
```

The same target fields as the service are accepted here: `entity_id`,
`device_id`, `area_id` and `ieee`.

The UI setup is recommended; the YAML block is only needed if you prefer to
configure the target by hand.

---

## Services

### `zha_midea_follow_me.send_follow_me`

Sends a Follow Me command to the air conditioner.

| Field | Required | Default | Description |
|---|---|---|---|
| `temperature` | yes | – | Target temperature. Rounded to the nearest whole degree before sending. |
| `entity_id` | no | configured blaster | Entity of the IR blaster (e.g. `switch.moes_ufo_r11_switch`). |
| `device_id` | no | configured blaster | Device id of the IR blaster in the device registry. |
| `area_id` | no | configured blaster | Area that contains the IR blaster. |
| `ieee` | no | configured blaster | IEEE address of the IR blaster (e.g. `00:0d:6f:00:05:7d:2d:34`). |
| `use_fahrenheit` | no | `false` | Send the value on the Fahrenheit scale instead of Celsius. |
| `beeper` | no | `false` | Let the AC beep when it applies the new setpoint. |

The `entity_id`, `device_id` and `area_id` fields are the standard Home
Assistant target fields, so they can also be given as a `target:` block. If
more than one is given, they are evaluated in the order `ieee`, `device_id`,
`entity_id`, `area_id`. If none is given, the blaster selected during setup is
used.

```yaml
action: zha_midea_follow_me.send_follow_me
data:
  temperature: 22.5
```

Target a specific blaster explicitly:

```yaml
action: zha_midea_follow_me.send_follow_me
target:
  entity_id: switch.moes_ufo_r11_switch
data:
  temperature: 22.5
  beeper: true
```

The same call with the blaster addressed by its IEEE address:

```yaml
action: zha_midea_follow_me.send_follow_me
data:
  ieee: 00:0d:6f:00:05:7d:2d:34
  temperature: 22.5
  beeper: true
```

### `zha_midea_follow_me.debug_follow_me`

Logs the generated Midea packet, the raw IR timings and the final Tuya IR
string without sending anything. Use it to verify the encoding.

| Field | Required | Default | Description |
|---|---|---|---|
| `temperature` | yes | – | Temperature value to evaluate. |
| `use_fahrenheit` | no | `false` | Evaluate on the Fahrenheit scale. |
| `beeper` | no | `false` | Evaluate with the beeper flag set. |

```yaml
action: zha_midea_follow_me.debug_follow_me
data:
  temperature: 22.5
```

The result is written to the Home Assistant log at `INFO` level:

```
Midea Follow Me Debug:
{
  "midea_packet": {
    "hex": "A4 82 48 7F 17 06",
    "bytes": [164, 130, 72, 127, 23, 6],
    "checksum": "0x06"
  },
  ...
}
```

---

## Automation example

Keep a room in sync with the temperature reported by a sensor in that room.
Also uses a hysteresis of 0.5 degrees to prevent cycling.

Requires the [Saver](https://github.com/piotrmachowski/home-assistant-custom-components-saver) component to store variables.

```yaml
alias: Midea PortaSplit Follow Me
description: ''
triggers:
  - trigger: time_pattern
    minutes: /3
conditions:
  - condition: state
    entity_id: climate.midea_portasplit
    state:
      - cool
      - heat
actions:
  - variables:
      current_temp: '{{ states(''sensor.temperature_upstairs_temperature'') | float(0) }}'
      last_sent: '{{ saver.variable(''last_sent_temp'') | default(-999, true) | float }}'
      temp_diff: '{{ (current_temp - last_sent) | abs }}'
      rounded_temp: '{{ current_temp | round }}'
  - choose:
      - conditions:
          - condition: template
            value_template: '{{ temp_diff >= 0.5 and rounded_temp != last_sent | round }}'
        sequence:
          - action: saver.set_variable
            data:
              name: last_sent_temp
              value: '{{ current_temp }}'
          - action: zha_midea_follow_me.send_follow_me
            metadata: {}
            target:
              entity_id: switch.moes_ufo_r11
            data:
              use_fahrenheit: false
              beeper: false
              temperature: '{{ rounded_temp }}'
      - conditions:
          - condition: template
            value_template: '{{ last_sent != -999 }}'
        sequence:
          - action: zha_midea_follow_me.send_follow_me
            metadata: {}
            target:
              entity_id: switch.moes_ufo_r11
            data:
              use_fahrenheit: false
              beeper: false
              temperature: '{{ last_sent | round }}'
mode: single

```

---

## How it works

The Midea Follow Me packet is six bytes:

| Byte | Value | Meaning |
|---|---|---|
| 0 | `0xA4` | Follow Me message type |
| 1 | `0x82` | Constant |
| 2 | `0x48` | Control flags (bit 5 `0x20` = Fahrenheit) |
| 3 | `0x7F` | Control flags (bit 7 `0x80` = beeper) |
| 4 | – | Encoded temperature (Celsius `value + 1`, Fahrenheit `value - 31`) |
| 5 | – | Checksum, XOR of bytes 0–4 |

The temperature is clamped to the range the air conditioner accepts:
`0–37 °C` or `32–99 °F`.

> Half degrees are rounded to the nearest even value, so `22.4` is sent as
> `22 °C` and `23.5` as `24 °C`. Use whole degrees if you need an exact
> setpoint.

The packet is expanded into raw IR timings (matching ESPHome's
`MideaProtocol::encode()`), compressed into the Tuya IR stream format and sent
to the blaster with the ZHA `IRSend` command (`0x02`) on cluster `0xE004`.

The blaster's `zha-quirks` implementation wraps the code in the Tuya IR
envelope itself, so only the raw base64 IR string is passed to the cluster.

---

## Troubleshooting

**"No ZHA device with the Zosung IR Control cluster (0xE004) was found."**
The blaster is not paired with ZHA, or ZHA does not recognise it as a
TS1201. Check the ZHA device list and make sure the device exposes the
`0xE004` cluster.

**"The ZHA integration is not ready yet."**
ZHA has not finished starting up. Wait a moment and try again, or restart
Home Assistant.

**"Device not found: …"**
The target passed to the service does not match a ZHA device. Use the entity
ID of a ZHA entity belonging to the blaster, the device id of the blaster, or
its IEEE address. If you did not configure a default blaster, a target is
required.

**The command is sent but nothing happens.**
The IR blaster needs a clear line of sight to the air conditioner and the
blaster's IR code library does not have to know the device – the raw signal is
sent as-is. Run `debug_follow_me` first to confirm a payload is produced, then
check the Home Assistant log for `IR Follow Me command sent successfully`.

**The AC shows the wrong temperature.**
Fahrenheit is encoded with a `0x20` flag in byte 2. Make sure
`use_fahrenheit` matches the unit you are sending.

---

## Legacy pyscript

The original implementation of this integration is kept in
[`zha_midea_follow_me.pyscript`](zha_midea_follow_me.pyscript) for reference.
It requires the `pyscript` integration and sends the command through the
`zha.issue_zigbee_cluster_command` service, which is admin-only and therefore
cannot be used from a normal user account. The custom integration in
`custom_components/` supersedes it.

---

## Credits

This integration is heavily inspired by the `midea_ac.follow_me` action of
[ESPHome](https://esphome.io/components/midea_ac.html). The Follow Me packet
and the way it is expanded into raw IR timings were translated from the
ESPHome implementation (`MideaProtocol::encode()`) to Python.

The conversion of the raw IR timings into the Tuya IR stream format was taken
from [vills' gist](https://gist.github.com/vills/590c154b377ac50acab079328e4ddaf9),
which is itself based on the incredible work of:

- [@mildsunrise](https://gist.github.com/mildsunrise/1d576669b63a260d2cff35fda63ec0b5)
- [@elupus](https://github.com/elupus/irgen)

Thank you!
