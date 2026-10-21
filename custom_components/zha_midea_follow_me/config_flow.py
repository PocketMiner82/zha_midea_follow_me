"""Config flow for the ZHA Midea Follow Me integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_IEEE, DOMAIN
from .zha_device import get_ir_blasters, zha_gateway_available


def _device_schema(blasters: dict[str, str]) -> vol.Schema:
    """Return a schema to pick one of the available IR blasters."""
    return vol.Schema({vol.Required(CONF_IEEE): vol.In(blasters)})


class MideaFollowMeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for ZHA Midea Follow Me."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if not zha_gateway_available(self.hass):
            return self.async_abort(reason="zha_not_ready")

        blasters = get_ir_blasters(self.hass)
        if not blasters:
            return self.async_abort(reason="no_ir_devices")

        if user_input is not None:
            ieee = user_input[CONF_IEEE]
            await self.async_set_unique_id(ieee)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=blasters[ieee],
                data={CONF_IEEE: ieee},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=_device_schema(blasters),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of an existing entry."""
        entry = self._get_reconfigure_entry()

        if not zha_gateway_available(self.hass):
            return self.async_abort(reason="zha_not_ready")

        blasters = get_ir_blasters(self.hass)
        if not blasters:
            return self.async_abort(reason="no_ir_devices")

        if user_input is not None:
            ieee = user_input[CONF_IEEE]
            for other in self._async_current_entries():
                if other.entry_id != entry.entry_id and other.unique_id == ieee:
                    return self.async_abort(reason="already_configured")
            return self.async_update_reload_and_abort(
                entry,
                unique_id=ieee,
                title=blasters[ieee],
                data={CONF_IEEE: ieee},
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_device_schema(blasters),
        )
