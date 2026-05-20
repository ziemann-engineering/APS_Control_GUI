"""
TCU (Temperature Control Unit) Driver

Thin wrapper around the ZE TCU library (`hardware/TCU.py`).

Usage
-----
    from hardware.tcu_driver import TCUDriver

    tcu = TCUDriver('COM7', channels=2)
    tcu.connect()
    tcu.set_temperature(1, 150.0)
    tcu.enable_channel(1)
    temp = tcu.get_temperature(1)
    tcu.disconnect()
"""

import logging
import os
import time
from typing import Optional

log = logging.getLogger(__name__)

try:
    from hardware.TCU import TCU as _TCU
    _tcu_lib_loaded = True
except ImportError:
    _TCU = None
    _tcu_lib_loaded = False
    log.warning(
        'hardware/TCU.py could not be imported. '
        'TCUDriver.connect() will raise ImportError at runtime.'
    )


class TCUDriver:
    """Thread-safe, logged wrapper around the ZE TCU class.

    Each TCUDriver instance manages one physical TCU device (one serial port).
    Multiple channels on the same TCU are accessed via channel index (1-based).

    Parameters
    ----------
    port:
        Serial port string, e.g. 'COM7' or '/dev/ttyUSB1'.
    channels:
        Number of channels on the physical TCU (default 2).
    baudrate:
        Baud rate (default 38400, must match TCU firmware).
    """

    def __init__(self, port: str, channels: int = 2, baudrate: int = 38400):
        self.port = port
        self.channels = channels
        self.baudrate = baudrate
        self._tcu = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Open serial port and verify the TCU handshake.

        Returns True on success, False on failure.
        """
        try:
            if _TCU is None:
                raise ImportError('hardware/TCU.py is not available')
            self._tcu = _TCU(self.port, channels=self.channels, baudrate=self.baudrate)
            log.info(f'TCU connected on {self.port}: {self._tcu.info.strip()}')
            return True
        except ImportError as exc:
            log.error(f'Cannot import TCU library: {exc}')
            return False
        except RuntimeError as exc:
            log.error(f'TCU handshake failed on {self.port}: {exc}')
            self._tcu = None
            return False
        except Exception as exc:
            log.error(f'TCU connect error on {self.port}: {exc}')
            self._tcu = None
            return False

    def disconnect(self):
        """Close the serial connection to the TCU."""
        if self._tcu is not None:
            try:
                self._tcu.close()
            except Exception as exc:
                log.warning(f'TCU close error: {exc}')
            self._tcu = None

    @property
    def connected(self) -> bool:
        return self._tcu is not None and self._tcu.connected

    # ------------------------------------------------------------------
    # Temperature control
    # ------------------------------------------------------------------

    def set_temperature(self, channel: int, temperature_c: float):
        """Set the temperature setpoint for *channel* (1-based).

        Parameters
        ----------
        channel:
            TCU channel index (1-based).
        temperature_c:
            Target temperature in degrees Celsius.
        """
        if self._tcu is None:
            log.warning('TCU not connected; set_temperature ignored')
            return
        self._tcu.set_temperature(channel, temperature_c)
        log.debug(f'TCU ch{channel} setpoint → {temperature_c} °C')

    def get_temperature(self, channel: int) -> Optional[float]:
        """Read the actual temperature for *channel* (1-based).

        Returns the temperature in degrees Celsius, or None on error.
        """
        if self._tcu is None:
            return None
        temp = self._tcu.get_temperature(channel)
        return temp  # TCU.get_temperature already returns nan on error

    def enable_channel(self, channel: int):
        """Enable the heater on *channel* (1-based)."""
        if self._tcu is None:
            log.warning('TCU not connected; enable_channel ignored')
            return
        self._tcu.enable_channel(channel)
        log.info(f'TCU ch{channel} enabled')

    def disable_channel(self, channel: int):
        """Disable the heater on *channel* (1-based)."""
        if self._tcu is None:
            log.warning('TCU not connected; disable_channel ignored')
            return
        self._tcu.disable_channel(channel)
        log.info(f'TCU ch{channel} disabled')

    # ------------------------------------------------------------------
    # Device discovery (no active connection needed)
    # ------------------------------------------------------------------

    @staticmethod
    def probe_port(port: str, baudrate: int = 38400, timeout: float = 0.5) -> Optional[dict]:
        """Non-destructive probe to identify a TCU on *port*.

        Sends 'ID\\r\\n' and checks for 'TCU,SN:<sn>' in the response.
        The port is always closed afterwards.

        Returns dict with keys ``device_type``, ``port``, ``serial``,
        ``label``; or *None* if not a TCU.
        """
        try:
            import serial as _serial
            with _serial.Serial(port, baudrate, timeout=timeout) as ser:
                ser.reset_input_buffer()
                ser.write(b'ID\r\n')
                time.sleep(0.3)
                data = ser.read(256).decode('ascii', errors='ignore')
            if 'TCU,SN:' in data:
                sn = data.split('TCU,SN:')[1].split('\n')[0].strip().rstrip('>')
                return {'device_type': 'tcu', 'port': port, 'serial': sn,
                        'label': f'TCU  SN:{sn}  ({port})'}
        except Exception:
            pass
        return None
