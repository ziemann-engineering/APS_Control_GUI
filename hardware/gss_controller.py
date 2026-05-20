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


import datetime

# Update compatible_build_date when a new firmware release is required.
# The software will warn if the connected board's firmware is older than this date.
compatible_build_date = (2026, 1, 1)  # (year, month, day)
compatible_board_type = "GSS Control Board"
compatible_manufacturer = "Ziemann Engineering"

# Directory containing firmware .bin files.
# Resolved relative to this file's location (hardware/ -> Python Software/ -> firmware/).
FIRMWARE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'firmware'
)


def _parse_build_date(date_str: str) -> Optional[datetime.date]:
    """Parse a build date string as returned by the firmware (*IDN? response).

    Handles formats produced by the C ``__DATE__`` macro, e.g. ``"May 17 2026"``
    or ``"May  7 2026"`` (single-digit day padded with a space), as well as
    ``YYYY-MM-DD`` for manually formatted dates.
    Returns a :class:`datetime.date` or ``None`` on failure.
    """
    # Strip extra whitespace (single-digit days get double-spaced by __DATE__)
    date_str = ' '.join(date_str.split())
    for fmt in ('%b %d %Y', '%B %d %Y', '%Y-%m-%d', '%d-%m-%Y', '%m/%d/%Y'):
        try:
            return datetime.datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def _parse_idn_response(response: str) -> Optional[dict]:
    """Parse a standard *IDN? response string.

    Expected format (firmware):
        ``Ziemann Engineering,GSS Control Board,<SN_hex>,<ver> / <build date>``

    Returns a dict with keys ``manufacturer``, ``device``, ``serial``,
    ``version``, ``build_date`` (a :class:`datetime.date` or ``None``),
    or ``None`` if parsing fails.
    """
    # Strip shell prompt artifacts ('>', newlines)
    line = response.strip().lstrip('>')
    for part in response.splitlines():
        part = part.strip().rstrip('>')
        if ',' in part:
            line = part
            break
    fields = [f.strip() for f in line.split(',')]
    if len(fields) < 4:
        return None
    manufacturer, device, serial, fw_field = fields[0], fields[1], fields[2], ','.join(fields[3:])
    # Firmware version field: "0.1 / May 17 2026" — split on '/'
    version = fw_field.strip()
    build_date = None
    if '/' in fw_field:
        ver_part, date_part = fw_field.split('/', 1)
        version = ver_part.strip()
        build_date = _parse_build_date(date_part.strip())
    else:
        build_date = _parse_build_date(fw_field.strip())
    return {
        'manufacturer': manufacturer,
        'device': device,
        'serial': serial,
        'version': version,
        'build_date': build_date,
    }


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

            # Identify the board with *IDN? and validate manufacturer / device type.
            response = self._send_command('*IDN?')
            if response is None:
                self.disconnect()
                return False
            idn = _parse_idn_response(response)
            if idn is None or idn.get('device') != compatible_board_type:
                found = idn.get('device') if idn else response.strip()
                print(f'GSS board validation failed: expected "{compatible_board_type}", got "{found}"')
                self.disconnect()
                return False

            # Warn (but do not block) if firmware is older than the compatible date.
            bd = idn.get('build_date')
            min_date = datetime.date(*compatible_build_date)
            if bd is not None and bd < min_date:
                print(f'Warning: GSS firmware build date {bd} is older than required {min_date}')
            elif bd is None:
                print('Warning: could not parse firmware build date from *IDN? response')

            self.serial_number = idn.get('serial', '')
            self.fw_version = idn.get('version', '')
            self.fw_build_date = bd
            print(f'Connected to GSS controller on {self.port}  '
                  f'SN:{self.serial_number}  FW:{idn.get("version","")} / {bd}')
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
        """Send *IDN? and return parsed result dict, or None on failure.

        Returns keys: ``manufacturer``, ``device``, ``serial``, ``version``,
        ``build_date`` (datetime.date or None).
        """
        response = self._send_command('*IDN?')
        if response is None:
            return None
        return _parse_idn_response(response)

    @staticmethod
    def probe_port(port: str, baudrate: int = 38400, timeout: float = 0.5) -> Optional[dict]:
        """Non-destructive probe of *port* to identify a GSS or TCU device.

        Opens the port, sends ``*IDN?\\r\\n``, and parses the standard IDN
        response.  The port is always closed afterwards, regardless of result.

        Returns
        -------
        dict with keys ``device_type`` ('gss' or 'tcu'), ``port``, ``serial``,
        ``version``, ``build_date``, ``label``; or *None* if no recognised
        device answered.
        """
        try:
            import serial as _serial
            with _serial.Serial(port, baudrate, timeout=timeout) as ser:
                ser.reset_input_buffer()
                ser.write(b'*IDN?\r\n')
                time.sleep(0.3)
                data = ser.read(512).decode('ascii', errors='ignore')
            idn = _parse_idn_response(data)
            if idn is None:
                return None
            device = idn.get('device', '')
            sn = idn.get('serial', '')
            ver = idn.get('version', '')
            bd = idn.get('build_date')
            if 'GSS' in device:
                return {'device_type': 'gss', 'port': port, 'serial': sn,
                        'version': ver, 'build_date': bd,
                        'label': f'GSS  SN:{sn}  {ver}  ({port})'}
            if 'TCU' in device:
                return {'device_type': 'tcu', 'port': port, 'serial': sn,
                        'version': ver, 'build_date': bd,
                        'label': f'TCU  SN:{sn}  {ver}  ({port})'}
        except Exception:
            pass
        return None

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
    def _read_build_date_from_bin(path: str) -> Optional[datetime.date]:
        """Extract the build date embedded in a firmware ``.bin`` file.

        The GCC compiler embeds ``__DATE__`` as a plain ASCII string whenever
        it is referenced in the source (e.g. in the ``*IDN?`` response).
        The string format is ``"Mmm  d yyyy"`` or ``"Mmm dd yyyy"`` (11 chars).

        Scans the binary for the first occurrence of that pattern and returns
        the parsed date, or ``None`` if not found.
        """
        _DATE_RE = re.compile(
            rb'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
            rb'[ \t]+\d{1,2}[ \t]+\d{4}'
        )
        try:
            with open(path, 'rb') as f:
                data = f.read()
            m = _DATE_RE.search(data)
            if m:
                return _parse_build_date(m.group(0).decode('ascii', errors='ignore'))
        except OSError:
            pass
        return None

    @staticmethod
    def find_firmware_update(current_build_date: 'datetime.date | str | None',
                             firmware_dir: str = FIRMWARE_DIR) -> Optional[str]:
        """Search *firmware_dir* for a GSS firmware ``.bin`` newer than *current_build_date*.

        The build date is read directly from each ``.bin`` file by locating the
        ``__DATE__`` string that the compiler embeds when it is referenced in
        source (e.g. in the ``*IDN?`` response).  No special filename convention
        is required — any ``.bin`` file in the directory is inspected.

        Parameters
        ----------
        current_build_date:
            Build date of the currently running firmware, as reported by
            ``*IDN?``.  Accepts a :class:`datetime.date`, an ISO-format string
            ``'YYYY-MM-DD'``, or ``None`` (any file with a parseable date is
            returned as an update candidate).

        Returns the path of the newest qualifying file, or *None* if no update
        is available or the directory does not exist.
        """
        if not os.path.isdir(firmware_dir):
            return None

        if isinstance(current_build_date, str):
            current_build_date = _parse_build_date(current_build_date)

        best_path: Optional[str] = None
        best_date: Optional[datetime.date] = current_build_date
        for fname in os.listdir(firmware_dir):
            if not fname.lower().endswith('.bin'):
                continue
            fpath = os.path.join(firmware_dir, fname)
            fdate = GSSController._read_build_date_from_bin(fpath)
            if fdate is None:
                continue
            if best_date is None or fdate > best_date:
                best_date = fdate
                best_path = fpath
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
