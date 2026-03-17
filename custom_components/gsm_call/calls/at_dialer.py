# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import asyncio

from homeassistant.exceptions import HomeAssistantError

from ..const import _LOGGER, EndedReason
from ..modem import Modem


class ATDialer:
    at_command = "ATD"

    def __init__(self, dial_timeout_sec: int, call_duration_sec: int):
        self._dial_sec = dial_timeout_sec
        self._call_sec = call_duration_sec

    async def dial(self, modem: Modem, phone_number: str) -> EndedReason:
        _LOGGER.debug(f"Dialing +{phone_number}...")
        try:
            lines = await modem.execute_at(
                f"{self.at_command}+{phone_number};",
                timeout=10,
                end_markers=["OK", "ERROR", "BUSY", "NO CARRIER", "+CME ERROR"]
            )
            reply = " ".join(lines)
            _LOGGER.debug(f"Modem replied with {reply}")

            if "BUSY" in reply:
                raise HomeAssistantError("Busy")

            if "ERROR" in reply or "NO CARRIER" in reply:
                raise HomeAssistantError(f"Modem replied with an error: {reply}")

            ended_reason = await self._wait_for_call_end(modem)

            _LOGGER.debug("Hanging up...")
            modem.send_command("AT+CHUP")
            _LOGGER.info(f"Call ended: {ended_reason}")

            return ended_reason
        except asyncio.TimeoutError:
            raise HomeAssistantError(f"Timeout while dialing +{phone_number}")

    async def _wait_for_call_end(self, modem: Modem) -> EndedReason:
        """Wait for the call to end by listening for modem unsolicited responses.

        First tries AT+CLCC polling. If the modem doesn't support it,
        falls back to passive listening for NO CARRIER / BUSY.
        """
        # Try AT+CLCC once to see if modem supports it
        lines = await modem.execute_at(
            "AT+CLCC", timeout=2, end_markers=["OK", "ERROR", "+CME ERROR"]
        )
        reply = " ".join(lines)
        _LOGGER.debug(f"CLCC probe: {reply}")

        if "+CLCC:" in reply or "OK" in reply and len(lines) > 1:
            # Modem supports CLCC, use polling
            return await self._poll_clcc(modem, reply)

        # Modem doesn't support CLCC, use passive wait
        _LOGGER.info(f"AT+CLCC not supported, waiting passively for {self._call_sec}s...")
        return await self._passive_wait(modem)

    async def _poll_clcc(self, modem: Modem, initial_reply: str) -> EndedReason:
        """Poll AT+CLCC for call state changes."""
        is_ringing = False
        reply = initial_reply

        async with asyncio.timeout(self._dial_sec) as timeout:
            while True:
                if not is_ringing and "+CLCC: 1,0,3" in reply:
                    is_ringing = True
                    _LOGGER.info(f"Phone ringing, waiting {self._call_sec}s...")
                    timeout.reschedule(asyncio.get_running_loop().time() + self._call_sec)

                elif "+CLCC: 1,0,0" in reply:
                    return EndedReason.ANSWERED

                elif "ERROR" in reply:
                    # +CME ERROR during call = network ended the call
                    _LOGGER.debug("Modem returned error during poll, treating as not answered")
                    return EndedReason.NOT_ANSWERED

                elif is_ringing and "+CLCC:" not in reply:
                    # Only OK, no +CLCC line = callee actively declined
                    return EndedReason.DECLINED

                await asyncio.sleep(1)
                lines = await modem.execute_at(
                    "AT+CLCC", timeout=2, end_markers=["OK", "ERROR", "+CME ERROR"]
                )
                reply = " ".join(lines)
                _LOGGER.debug(f"CLCC poll: {reply}")

    async def _passive_wait(self, modem: Modem) -> EndedReason:
        """Wait passively for call_duration_sec, listening for modem URCs."""
        try:
            async with asyncio.timeout(self._call_sec):
                while True:
                    line = await modem.reader.readline()
                    decoded = line.decode(errors="ignore").strip()
                    if not decoded:
                        continue
                    _LOGGER.debug(f"Modem URC: {decoded}")
                    if "NO CARRIER" in decoded:
                        return EndedReason.DECLINED
                    if "BUSY" in decoded:
                        return EndedReason.DECLINED
        except TimeoutError:
            return EndedReason.NOT_ANSWERED
