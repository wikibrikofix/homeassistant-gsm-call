# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_DEVICE

from .const import (
    CONF_CALL_DURATION_SEC,
    CONF_DIAL_TIMEOUT_SEC,
    CONF_HARDWARE,
    CONF_TYPE,
    DEFAULT_CALL_DURATION_SEC,
    DEFAULT_DIAL_TIMEOUT_SEC,
    DEFAULT_HARDWARE,
    DOMAIN,
)


class GsmCallConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for GSM Call."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            device = user_input[CONF_DEVICE]
            if not device:
                errors[CONF_DEVICE] = "no_device"
            else:
                notify_type = user_input.get(CONF_TYPE, "call")
                await self.async_set_unique_id(f"{device}_{notify_type}")
                self._abort_if_unique_id_configured()
                title = f"GSM {'SMS' if notify_type == 'sms' else 'Call'} ({device.split('/')[-1]})"
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE): str,
                    vol.Required(CONF_TYPE, default="call"): vol.In({"call": "Voice Call", "sms": "SMS"}),
                    vol.Required(CONF_HARDWARE, default=DEFAULT_HARDWARE): vol.In({"atd": "Default (ATD)", "atdt": "Tone dialing (ATDT)", "zte": "ZTE", "gtm382": "GTM382"}),
                    vol.Optional(CONF_DIAL_TIMEOUT_SEC, default=DEFAULT_DIAL_TIMEOUT_SEC): vol.All(int, vol.Range(min=5, max=120)),
                    vol.Optional(CONF_CALL_DURATION_SEC, default=DEFAULT_CALL_DURATION_SEC): vol.All(int, vol.Range(min=5, max=300)),
                }
            ),
            errors=errors,
        )
