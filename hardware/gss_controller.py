"""
GSS Controller Interface Library

Interfaces with the GSS (Gate Switching Stress) control board via serial
communication.  Uses the same shell-prompt protocol as the APS controller.

Firmware protocol (one command active at a time):
  GSS_test <cycles> <freq_hz> <duty>  — runs one batch, blocks until done,
                                        returns "TEST_COMPLETE <total>"
  GSS_cycles                          — returns "CYCLES <total>"
  measure_supply                      — returns "POS:+x.xx NEG:y.yy"
  measure_DUT <0-8>                   — returns "OK" (0 = deselect all)
  ID                                  — returns "GSS,SN:XX,VER:0.1"
  status                              — returns running state
  stop                                — aborts between batches
  dfu                                 — reboot into USB DFU bootloader
  reset                               — MCU software reset
"""

import json
import os
import re
import serial
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple


# Update these when the firmware version that the software is compatible with
# is known.
compatible_build_date = (2026, 1, 1)
compatible_board_type = "GSS Control Board"

# Directory containing GSS_CONTROL_v{major}.{minor}.bin firmware files.
# Resolved relative to this file's location (hardware/ -> Python Software/ -> firmware/).
FIRMWARE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'firmware'
)


def _parse_version(version_str: str) -> Tuple[int, int]:
    """Parse 'major.minor' string into an (int, int) tuple for comparison."""
    try:
        parts = version_str.strip().split('.')
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        return (0, 0)


class GSSControllerError(Exception):
    """Base exception for GSS controller errors."""


class GSSCommunicationError(GSSControllerError):
    """Communication error with the GSS controller."""


@dataclass
class GSSStatus:
    """Snapshot of GSS controller state."""
    running: bool
    cycle_count: int
    freq_hz: float
    duty_cycle: float
    error: Optional[str] = None


class GSSController:
    """
    Interface to the GSS Control Board via serial communication.

    Uses the same shell-prompt-based serial protocol as the APS controller
    (38400 baud, commands terminated with CR+LF, responses end with '>').

    Methods that send firmware-specific commands are stubs that raise
    NotImplementedError until the firmware protocol is finalised.
    """

    # -----------------------------------------------------------------------
    # Construction & connection
    # -----------------------------------------------------------------------

    @staticmethod
    def _visa_to_com_port(resource_string: str) -> str:
        """Convert a VISA ASRL resource string to a COM / device path.

        Examples
        --------
        'ASRL7::INSTR'              -> 'COM7'
        'ASRL/dev/ttyUSB0::INSTR'   -> '/dev/ttyUSB0'
        'COM7'                      -> 'COM7'   (unchanged)
        '/dev/ttyUSB0'              -> '/dev/ttyUSB0'  (unchanged)
        """
        if resource_string.upper().startswith('COM') or resource_string.startswith('/dev/'):
            return resource_string

        match = re.match(r'ASRL(/dev/[^:]+)::INSTR', resource_string, re.IGNORECASE)
        if match:
            return match.group(1)

        match = re.match(r'ASRL(\d+)::INSTR', resource_string, re.IGNORECASE)
        if match:
            return f'COM{match.group(1)}'

        return resource_string

    def __init__(self, port: str, baudrate: int = 38400, timeout: float = 1.0):
        """
        Parameters
        ----------
        port:
            Serial port or VISA ASRL resource string.
        baudrate:
            Baud rate (default 38400, same as APS controller).
        timeout:
            Per-command timeout in seconds.
        """
        self.port = self._visa_to_com_port(port)
        self.original_port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial_conn: Optional[serial.Serial] = None
        self._lock = threading.Lock()

    def connect(self) -> bool:
        """Open the serial port and verify the controller responds.

        Returns True on success, False otherwise.
        """
        print(f'Connecting to GSS controller on {self.port} at {self.baudrate} baud')
        try:
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS,
            )
            time.sleep(0.1)

            # A "status" command is expected on the GSS board as well.
            # TODO: update handshake logic when firmware is finalised.
            response = self._send_command('status')
            if response is None:
                self.disconnect()
                return False

            print(f'Connected to GSS controller on {self.port}')
            return True

        except Exception as exc:
            print(f'Failed to connect to GSS controller on {self.port}: {exc}')
            return False

    def disconnect(self):
        """Close the serial connection."""
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
            self.serial_conn = None

    # -----------------------------------------------------------------------
    # Low-level communication helpers (same pattern as APS controller)
    # -----------------------------------------------------------------------

    def _send_command(self, command: str, timeout: Optional[float] = None) -> Optional[str]:
        """Send a command and return the response text (up to and including '>').

        Raises GSSCommunicationError on serial errors.
        """
        if not self.serial_conn or not self.serial_conn.is_open:
            raise GSSCommunicationError('Not connected to GSS controller')

        with self._lock:
            try:
                self.serial_conn.reset_input_buffer()
                self.serial_conn.write((command + '\r\n').encode('ascii'))
                self.serial_conn.flush()

                response_timeout = timeout if timeout is not None else self.timeout
                start_time = time.time()
                lines = []

                while time.time() - start_time < response_timeout:
                    if self.serial_conn.in_waiting > 0:
                        line = self.serial_conn.readline().decode('ascii', errors='ignore').strip()
                        if line:
                            lines.append(line)
                            if line.endswith('>'):
                                break
                    else:
                        time.sleep(0.01)

                return '\n'.join(lines) if lines else None

            except Exception as exc:
                raise GSSCommunicationError(f'Communication error: {exc}')

    def read_message(self, timeout: float = 0.1) -> Optional[str]:
        """Non-blocking read of one async message line from the controller.

        Returns the stripped line, or None if no data is available within
        *timeout* seconds.
        """
        if not self.serial_conn or not self.serial_conn.is_open:
            return None
        try:
            old_timeout = self.serial_conn.timeout
            self.serial_conn.timeout = timeout
            try:
                if self.serial_conn.in_waiting > 0:
                    line = self.serial_conn.readline().decode('ascii', errors='ignore').strip()
                    return line if line else None
                return None
            finally:
                self.serial_conn.timeout = old_timeout
        except Exception:
            return None

    # -----------------------------------------------------------------------
    # GSS-specific commands
    # NOTE: All command strings below are TBD.  Replace the NotImplementedError
    #       raises (and the TODO comments) with actual firmware commands once
    #       the protocol is finalised.
    # -----------------------------------------------------------------------

    def get_id(self) -> Optional[dict]:
        """Send ID command and return {'type': 'GSS', 'serial': '<sn>', 'version': '<ver>'} or None.

        Firmware responds with 'GSS,SN:<sn>,VER:<ver>' followed by the shell prompt.
        """
        response = self._send_command('ID')
        if response and 'GSS,SN:' in response:
            sn_part = response.split('GSS,SN:')[1]
            sn = sn_part.split(',')[0].split()[0].rstrip('>')
            ver = '0.0'
            if 'VER:' in response:
                ver = response.split('VER:')[1].split()[0].strip().rstrip('>')
            return {'type': 'GSS', 'serial': sn, 'version': ver}
        return None

    @staticmethod
    def probe_port(port: str, baudrate: int = 38400, timeout: float = 0.5) -> Optional[dict]:
        """Non-destructive probe of *port* to identify a GSS or TCU device.

        Opens the port, sends 'ID\\r\\n', and parses the response.  The port
        is always closed afterwards, regardless of result.

        Returns
        -------
        dict with keys ``device_type`` ('gss' or 'tcu'), ``port``, ``serial``,
        ``label``; or *None* if no recognised device answered.
        """
        try:
            import serial as _serial
            with _serial.Serial(port, baudrate, timeout=timeout) as ser:
                ser.reset_input_buffer()
                ser.write(b'ID\r\n')
                time.sleep(0.3)
                data = ser.read(256).decode('ascii', errors='ignore')
            if 'GSS,SN:' in data:
                sn_part = data.split('GSS,SN:')[1].split('\n')[0].strip()
                sn = sn_part.split(',')[0].rstrip('>')
                ver = '0.0'
                if 'VER:' in data:
                    ver = data.split('VER:')[1].split()[0].strip().rstrip('>')
                return {'device_type': 'gss', 'port': port, 'serial': sn,
                        'version': ver,
                        'label': f'GSS  SN:{sn}  v{ver}  ({port})'}
            if 'TCU,SN:' in data:
                sn = data.split('TCU,SN:')[1].split('\n')[0].strip().rstrip('>')
                return {'device_type': 'tcu', 'port': port, 'serial': sn,
                        'label': f'TCU  SN:{sn}  ({port})'}
        except Exception:
            pass
        return None

    def info(self) -> Optional[str]:
        """Return raw firmware / board info string.

        TODO: confirm command name ('info'?) with firmware.
        """
        # TODO: update command string when firmware protocol is finalised
        return self._send_command('info')

    def get_status(self) -> Optional[str]:
        """Return raw status string from controller."""
        return self._send_command('status')

    def run_batch(self, cycles: int, freq_hz: float, duty_cycle: float,
                  extra_timeout_s: float = 10.0) -> Optional[int]:
        """Run one batch of *cycles* switching cycles and block until done.

        Parameters
        ----------
        cycles:
            Number of switching cycles to run.
        freq_hz:
            Switching frequency in Hz.
        duty_cycle:
            Duty cycle, 0.0 – 1.0 (exclusive).
        extra_timeout_s:
            Additional seconds added to the expected batch duration as serial
            timeout margin.  Default 10 s.

        Returns
        -------
        Total accumulated cycle count reported by the firmware, or None on
        communication error.

        Raises
        ------
        GSSCommunicationError
            On serial errors.
        ValueError
            If parameters are out of range.
        """
        if cycles <= 0:
            raise ValueError('cycles must be > 0')
        if freq_hz <= 0.0:
            raise ValueError('freq_hz must be > 0')
        if not (0.0 < duty_cycle < 1.0):
            raise ValueError('duty_cycle must be in (0, 1)')

        batch_duration_s = cycles / freq_hz
        timeout = batch_duration_s + extra_timeout_s

        cmd = f'GSS_test {cycles} {freq_hz:.6g} {duty_cycle:.6g}'
        response = self._send_command(cmd, timeout=timeout)
        if response is None:
            return None
        # Response contains "TEST_COMPLETE <total>" before the shell prompt
        for line in response.splitlines():
            m = re.search(r'TEST_COMPLETE\s+(\d+)', line)
            if m:
                return int(m.group(1))
        return None

    def enter_dfu(self) -> None:
        """Send the dfu command.  The MCU pulls BOOT0 high via a capacitor and
        resets into the USB DFU ROM bootloader.  The serial connection is lost
        immediately; any communication error is suppressed.
        """
        try:
            self._send_command('dfu', timeout=2.0)
        except Exception:
            pass

    @staticmethod
    def find_firmware_update(current_version: str,
                             firmware_dir: str = FIRMWARE_DIR) -> Optional[str]:
        """Search *firmware_dir* for a GSS firmware file newer than *current_version*.

        Files must match the pattern ``GSS_CONTROL_v{major}.{minor}.bin``.

        Returns the path of the newest qualifying file, or *None* if no update
        is available or the directory does not exist.
        """
        if not os.path.isdir(firmware_dir):
            return None
        cur = _parse_version(current_version)
        best_path: Optional[str] = None
        best_ver: Tuple[int, int] = cur
        pattern = re.compile(r'GSS_CONTROL_v(\d+)\.(\d+)\.bin$', re.IGNORECASE)
        for fname in os.listdir(firmware_dir):
            m = pattern.match(fname)
            if not m:
                continue
            fver = (int(m.group(1)), int(m.group(2)))
            if fver > best_ver:
                best_ver = fver
                best_path = os.path.join(firmware_dir, fname)
        return best_path

    @staticmethod
    def run_dfu_update(firmware_path: str,
                       timeout_s: float = 60.0) -> Tuple[bool, str]:
        """Flash *firmware_path* using dfu-util.

        Requires dfu-util to be on PATH (or specify full path in dfu_util_exe).

        Returns
        -------
        (True, message) on success, (False, error_message) on failure.
        """
        cmd = [
            'dfu-util', '-a', '0',
            '-s', '0x08000000:leave',
            '-D', firmware_path,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_s
            )
            if result.returncode == 0:
                return (True, 'Firmware flashed successfully.')
            err = (result.stderr or result.stdout or 'unknown error').strip()
            return (False, err)
        except FileNotFoundError:
            return (False,
                    'dfu-util not found.  Install it and ensure it is on PATH.')
        except subprocess.TimeoutExpired:
            return (False, f'DFU update timed out after {timeout_s:.0f} s.')
        except Exception as exc:
            return (False, str(exc))

    def stop(self) -> bool:
        """Send stop command.  Only effective between batches (shell is blocked
        during a running batch).  Returns True if the controller acknowledged.
        """
        response = self._send_command('stop')
        return response is not None and 'OK' in response

    def get_cycle_count(self) -> Optional[int]:
        """Read total accumulated switching cycle count from the controller.

        Returns cycle count as int, or None on parse error.
        """
        response = self._send_command('GSS_cycles')
        if response is None:
            return None
        m = re.search(r'CYCLES\s+(\d+)', response)
        return int(m.group(1)) if m else None

    def get_output_voltages(self) -> tuple:
        """Read positive and negative gate supply voltages.

        Returns
        -------
        (v_pos_V, v_neg_V) as floats, or (None, None) on error.
        """
        response = self._send_command('measure_supply')
        if response is None:
            return (None, None)
        m = re.search(r'POS:\+?([\d.]+)\s+NEG:([\-\d.]+)', response)
        if m:
            return (float(m.group(1)), float(m.group(2)))
        return (None, None)

    def select_dut(self, dut_index: int) -> bool:
        """Connect DUT *dut_index* (1-based, 0 = deselect all) to SMU path.

        Returns True on success.
        """
        if not (0 <= dut_index <= 8):
            raise ValueError('dut_index must be 0-8')
        response = self._send_command(f'measure_DUT {dut_index}')
        return response is not None and 'OK' in response

    def configure(self, freq_hz: float, duty_cycle: float) -> None:
        """Deprecated stub — parameters are now passed directly to run_batch()."""
        raise NotImplementedError(
            'configure() is replaced by run_batch(cycles, freq_hz, duty_cycle).'
        )

    def start(self) -> None:
        """Deprecated stub — use run_batch() instead."""
        raise NotImplementedError(
            'start() is replaced by run_batch(cycles, freq_hz, duty_cycle).'
        )


# ---------------------------------------------------------------------------
# GSSTestSession — batch sequencer with crash-safe state persistence
# ---------------------------------------------------------------------------

@dataclass
class GSSTestSession:
    """Manages a multi-batch GSS stress test with periodic state saves.

    State is written to *state_file* (JSON) after every batch and at least
    every *save_interval_s* seconds via a background timer, so a GUI crash
    can resume from the last completed batch.

    Parameters
    ----------
    controller:
        Connected :class:`GSSController` instance.
    target_cycles:
        Total switching cycles to accumulate across all batches.
    batch_cycles:
        Cycles per individual batch (sent as one GSS_test command).
    freq_hz:
        Switching frequency in Hz.
    duty_cycle:
        Duty cycle 0.0 – 1.0.
    state_file:
        Path for the JSON checkpoint file.
    save_interval_s:
        Maximum seconds between periodic state saves (default 60).

    Usage
    -----
    >>> sess = GSSTestSession(ctrl, target_cycles=1_000_000, batch_cycles=10_000,
    ...                       freq_hz=10_000, duty_cycle=0.5,
    ...                       state_file='gss_session.json')
    >>> sess.load()          # resume if a checkpoint exists
    >>> for result in sess.run():
    ...     # result is a dict with 'completed', 'v_pos', 'v_neg'
    ...     do_vth_measurement(result)
    """

    controller:      GSSController
    target_cycles:   int
    batch_cycles:    int
    freq_hz:         float
    duty_cycle:      float
    state_file:      str
    save_interval_s: float = 60.0

    # runtime state (not constructor params)
    completed_cycles: int = field(default=0, init=False)
    batch_number:     int = field(default=0, init=False)
    _save_timer:      Optional[threading.Timer] = field(default=None, init=False, repr=False)

    def load(self) -> bool:
        """Load checkpoint from *state_file* if it exists.

        Returns True if a checkpoint was found and loaded.
        """
        if not os.path.exists(self.state_file):
            return False
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.completed_cycles = int(data.get('completed_cycles', 0))
            self.batch_number     = int(data.get('batch_number', 0))
            print(f'GSSTestSession: resumed from checkpoint — '
                  f'{self.completed_cycles} cycles done, batch {self.batch_number}')
            return True
        except Exception as exc:
            print(f'GSSTestSession: could not load checkpoint: {exc}')
            return False

    def save(self) -> None:
        """Write current state to *state_file*."""
        data = {
            'completed_cycles': self.completed_cycles,
            'batch_number':     self.batch_number,
            'target_cycles':    self.target_cycles,
            'batch_cycles':     self.batch_cycles,
            'freq_hz':          self.freq_hz,
            'duty_cycle':       self.duty_cycle,
            'saved_at':         time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
        tmp = self.state_file + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.state_file)   # atomic on most platforms

    def _schedule_save(self) -> None:
        """Arm a one-shot timer to call save() after *save_interval_s*."""
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(self.save_interval_s, self._periodic_save)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _periodic_save(self) -> None:
        self.save()
        self._schedule_save()

    def run(self):
        """Generator that runs batches until *target_cycles* is reached.

        Yields a result dict after each batch::

            {
                'batch':     <int>,   # 1-based batch number
                'completed': <int>,   # cumulative cycles after this batch
                'remaining': <int>,   # cycles still to run
                'v_pos':     <float>, # positive supply (V), None on error
                'v_neg':     <float>, # negative supply (V), None on error
            }

        The caller is responsible for Vth / other measurements between yields.
        """
        self._schedule_save()
        try:
            while self.completed_cycles < self.target_cycles:
                remaining_total = self.target_cycles - self.completed_cycles
                this_batch = min(self.batch_cycles, remaining_total)

                total = self.controller.run_batch(
                    cycles=this_batch,
                    freq_hz=self.freq_hz,
                    duty_cycle=self.duty_cycle,
                )
                if total is None:
                    raise GSSCommunicationError(
                        f'run_batch() returned None on batch {self.batch_number + 1}'
                    )

                self.completed_cycles += this_batch
                self.batch_number     += 1

                v_pos, v_neg = self.controller.get_output_voltages()

                self.save()   # checkpoint after every batch

                yield {
                    'batch':     self.batch_number,
                    'completed': self.completed_cycles,
                    'remaining': self.target_cycles - self.completed_cycles,
                    'v_pos':     v_pos,
                    'v_neg':     v_neg,
                }
        finally:
            if self._save_timer is not None:
                self._save_timer.cancel()
