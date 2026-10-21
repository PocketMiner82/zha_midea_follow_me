"""Shared helpers for the ZHA Midea Follow Me integration."""
from homeassistant.core import HomeAssistant

from .const import DATA_CONFIG_ENTRIES, DATA_TARGET, DOMAIN


def get_configured_targets(hass: HomeAssistant) -> dict[str, str]:
    """Return a mapping of config entry id to the configured IEEE address."""
    return hass.data.get(DOMAIN, {}).get(DATA_CONFIG_ENTRIES, {})


def get_default_target(hass: HomeAssistant) -> str | None:
    """Return the IEEE address to use when a service call does not name one.

    The blaster configured through the UI (or YAML) is used. When more than
    one blaster is configured the target is ambiguous and None is returned.
    """
    configured = get_configured_targets(hass)
    if len(configured) == 1:
        target = next(iter(configured.values()))
        if target:
            return target

    return hass.data.get(DOMAIN, {}).get(DATA_TARGET)
