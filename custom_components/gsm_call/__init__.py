# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE, Platform
from homeassistant.core import HomeAssistant

from .const import CONF_HARDWARE, CONF_TYPE, DOMAIN

PLATFORMS = [Platform.NOTIFY]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up GSM Call from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "device": entry.data[CONF_DEVICE],
        "type": entry.data.get(CONF_TYPE, "call"),
        "hardware": entry.data.get(CONF_HARDWARE, "atd"),
        "config": entry.data,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
