"""Driver for Ziemann Engineering TCUv2 temperature controllers."""

import logging
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import serial

log = logging.getLogger(__name__)


@dataclass
class Channel:
    enabled: bool = False
    actual_temperature: float = 0.0
    setpoint_temperature: Optional[float] = None


class ZETCUv2:
    """Control one ZE TCUv2 over its serial connection."""

    def __init__(self, port: str, channels: int = 2, baudrate: int = 38400):
        self.port = port
        self.channels = channels
        self.baudrate = baudrate
        self.connected = False
        self.info = ''
        self.channel = [Channel() for _ in range(channels + 1)]
        self.ser = None

    def __enter__(self):
        if not self.connected and not self.connect():
            raise RuntimeError(f'Could not connect to ZE TCUv2 on {self.port}')
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.disconnect()

    def connect(self) -> bool:
        """Open the serial port and verify the controller identity."""
        try:
            self.ser = serial.Serial(
                self.port, self.baudrate, timeout=1, write_timeout=1,
            )
            response = self._command('*IDN?')
            if 'ZE TCU' not in response and 'TCU' not in response:
                raise RuntimeError(
                    f'This does not seem to be a ZE TCU, received: {response}'
                )
            self.info = response.strip()
            self.connected = True
            log.info(f'TCU connected on {self.port}: {self.info}')
            return True
        except Exception as exc:
            log.error(f'TCU connect error on {self.port}: {exc}')
            self.disconnect()
            return False

    def disconnect(self):
        """Close the serial connection."""
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception as exc:
                log.warning(f'TCU close error on {self.port}: {exc}')
        self.ser = None
        self.connected = False

    close = disconnect

    def _command(self, text: str) -> str:
        if self.ser is None:
            return ''
        self.ser.write(f'{text}\n'.encode())
        deadline = time.monotonic() + self.ser.timeout
        while time.monotonic() < deadline:
            response = self.ser.readline().decode(errors='replace').strip()
            if not response or response == text:
                continue
            return response
        return ''

    def serialwrite(self, text: str) -> str:
        return self._command(text) if self.connected else ''

    def get_temperature(self, channel: int) -> Optional[float]:
        response = self.serialwrite(f'T? {channel}')
        if not response:
            log.warning(f'TCU ch{channel}: no temperature response')
            return np.nan
        try:
            temperature = float(response)
        except ValueError:
            log.warning(f'TCU ch{channel}: invalid temperature response {response!r}')
            return np.nan
        self.channel[channel].actual_temperature = temperature
        return temperature

    def set_temperature(self, channel: int, temperature_c: float):
        response = self.serialwrite(f'T_set {channel} {temperature_c:g}')
        if not response.startswith(f'Channel {channel} setpoint set to'):
            raise RuntimeError(f'TCU rejected setpoint: {response}')
        self.channel[channel].setpoint_temperature = temperature_c
        log.debug(f'TCU ch{channel} setpoint -> {temperature_c} C')

    def enable_channel(self, channel: int):
        response = self.serialwrite(f'Ch {channel} on')
        if response != f'Channel {channel} enabled.':
            raise RuntimeError(f'TCU rejected channel enable: {response}')
        self.channel[channel].enabled = True
        log.info(f'TCU ch{channel} enabled')

    def disable_channel(self, channel: int):
        response = self.serialwrite(f'Ch {channel} off')
        if response != f'Channel {channel} disabled.':
            raise RuntimeError(f'TCU rejected channel disable: {response}')
        self.channel[channel].enabled = False
        log.info(f'TCU ch{channel} disabled')

    @staticmethod
    def probe_port(
        port: str, baudrate: int = 38400, timeout: float = 0.5,
    ) -> Optional[dict]:
        """Return TCU identity details for *port*, or ``None`` when absent."""
        try:
            with serial.Serial(port, baudrate, timeout=timeout) as ser:
                ser.reset_input_buffer()
                ser.write(b'*IDN?\n')
                deadline = time.monotonic() + timeout
                response = ''
                while time.monotonic() < deadline:
                    line = ser.readline().decode('ascii', errors='ignore').strip()
                    if line and line != '*IDN?':
                        response = line
                        break
            if 'TCU' not in response:
                return None
            fields = [field.strip() for field in response.split(',')]
            serial_number = fields[2] if len(fields) > 2 else 'unknown'
            return {
                'device_type': 'tcu',
                'port': port,
                'serial': serial_number,
                'label': f'TCU  SN:{serial_number}  ({port})',
            }
        except Exception:
            return None