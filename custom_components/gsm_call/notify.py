# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

import re

import asyncio

import serial
import serial_asyncio_fast as serial_asyncio
from homeassistant.components.notify import NotifyEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .calls.at_dialer import ATDialer
from .calls.at_tone_dialer import ATToneDialer
from .calls.gtm382_dialer import GTM382Dialer
from .calls.zte_dialer import ZTEDialer
from .const import (
    _LOGGER,
    ATTR_PHONE_NUMBER,
    ATTR_REASON,
    CONF_CALL_DURATION_SEC,
    CONF_DIAL_TIMEOUT_SEC,
    DEFAULT_CALL_DURATION_SEC,
    DEFAULT_DIAL_TIMEOUT_SEC,
    DOMAIN,
    EVENT_GSM_CALL_ENDED,
    EndedReason,
    GSM_7BIT_ALPHABET,
)
from .modem import READ_LIMIT, Modem
from .sms.sms_sender import SmsSender

SUPPORTED_DIALERS = {
    "atd": ATDialer,
    "atdt": ATToneDialer,
    "zte": ZTEDialer,
    "gtm382": GTM382Dialer,
}

PHONE_NUMBER_RE = re.compile(r"^\+?[1-9]\d{1,14}$")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GSM Call notify entities from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    device_path = data["device"]
    notify_type = data["type"]
    hardware = data["hardware"]
    config = data["config"]

    if notify_type == "sms":
        async_add_entities([GsmSmsNotifyEntity(entry, device_path)])
    else:
        dial_timeout = config.get(CONF_DIAL_TIMEOUT_SEC, DEFAULT_DIAL_TIMEOUT_SEC)
        call_duration = config.get(CONF_CALL_DURATION_SEC, DEFAULT_CALL_DURATION_SEC)
        dialer = SUPPORTED_DIALERS[hardware](
            dial_timeout_sec=dial_timeout,
            call_duration_sec=call_duration,
        )
        async_add_entities([GsmCallNotifyEntity(entry, device_path, dialer)])


def _validate_phone_number(phone_number: str) -> str:
    """Validate and normalize phone number."""
    if not PHONE_NUMBER_RE.match(phone_number):
        raise ValueError("Invalid phone number")
    return re.sub(r"\D", "", phone_number)


async def _connect(device_path: str) -> Modem:
    """Open serial connection and return a Modem instance."""
    _LOGGER.debug("Connecting to %s...", device_path)
    return Modem(
        *await serial_asyncio.open_serial_connection(
            url=device_path,
            baudrate=75600,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            dsrdtr=True,
            rtscts=True,
            limit=READ_LIMIT,
        )
    )


async def _disconnect(modem: Modem | None) -> None:
    """Close serial connection."""
    if modem is None:
        return
    _LOGGER.debug("Closing modem connection...")
    modem.writer.close()
    await modem.writer.wait_closed()


class GsmCallNotifyEntity(NotifyEntity):
    """Notify entity for GSM voice calls."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, device_path: str, dialer) -> None:
        self._entry = entry
        self._device_path = device_path
        self._dialer = dialer
        self._modem: Modem | None = None
        self._attr_unique_id = f"{entry.entry_id}_call"
        self._attr_name = "GSM Call"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"GSM Modem ({device_path.split('/')[-1]})",
            manufacturer="GSM Modem",
        )

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Make voice calls. Supports multiple numbers separated by |.

        Calls each number in sequence and stops when someone answers or
        declines. If nobody answers, cycles through all numbers once.
        """
        if not message or not message.strip():
            raise HomeAssistantError("Phone number is required in the message field")

        targets = [t.strip() for t in message.split("|") if t.strip()]
        if not targets:
            raise HomeAssistantError("Phone number is required in the message field")

        numbers = []
        for t in targets:
            try:
                numbers.append(_validate_phone_number(t))
            except ValueError as e:
                raise HomeAssistantError(f"Invalid phone number {t}: {e}") from e

        if self._modem:
            raise HomeAssistantError("Already making a voice call")

        try:
            self._modem = await _connect(self._device_path)
            for idx, phone_number in enumerate(numbers):
                _LOGGER.info("Calling +%s (%d/%d)...", phone_number, idx + 1, len(numbers))
                call_state = await self._dialer.dial(self._modem, phone_number)
                self.hass.bus.async_fire(
                    EVENT_GSM_CALL_ENDED,
                    {ATTR_PHONE_NUMBER: phone_number, ATTR_REASON: call_state},
                )
                if call_state == EndedReason.ANSWERED:
                    _LOGGER.info("Call to +%s was answered, stopping", phone_number)
                    break
                _LOGGER.info("No answer from +%s (%s), trying next number", phone_number, call_state)
                # Wait for modem to be ready for next call
                await asyncio.sleep(3)
        finally:
            await _disconnect(self._modem)
            self._modem = None


class GsmSmsNotifyEntity(NotifyEntity):
    """Notify entity for GSM SMS messages."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, device_path: str) -> None:
        self._entry = entry
        self._device_path = device_path
        self._sender = SmsSender()
        self._modem: Modem | None = None
        self._attr_unique_id = f"{entry.entry_id}_sms"
        self._attr_name = "GSM SMS"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"GSM Modem ({device_path.split('/')[-1]})",
            manufacturer="GSM Modem",
        )

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send an SMS. The message format is: 'phone_number|text'."""
        if not message:
            raise HomeAssistantError("Message is required")

        # Expect format: "+1234567890|SMS text here"
        if "|" not in message:
            raise HomeAssistantError("Message format: '+phone_number|text'")

        target, sms_text = message.split("|", 1)
        target = target.strip()
        sms_text = sms_text.strip()

        if not sms_text:
            raise HomeAssistantError("SMS text is required")

        if not re.match(GSM_7BIT_ALPHABET, sms_text):
            raise HomeAssistantError("Only basic Latin letters, digits, and common symbols are supported")

        try:
            phone_number = _validate_phone_number(target)
        except ValueError as e:
            raise HomeAssistantError(f"Invalid phone number {target}: {e}") from e

        if self._modem:
            raise HomeAssistantError("Already connected to the modem for SMS")

        try:
            self._modem = await _connect(self._device_path)
            await self._sender.send(self._modem, phone_number, sms_text)
        finally:
            await _disconnect(self._modem)
            self._modem = None
